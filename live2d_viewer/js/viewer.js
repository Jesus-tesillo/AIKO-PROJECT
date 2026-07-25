/**
 * viewer.js — Live2D Model Viewer Controller
 *
 * WHY THE PATCH APPROACH:
 *   The Cubism SDK calls loadParameters() every frame which resets ALL
 *   params to saved defaults, then motions/expressions/physics overwrite
 *   their specific params.  If we set params in a PixiJS ticker (which
 *   runs BEFORE the model's _render → internalModel.update()), the
 *   Cubism pipeline immediately overwrites our values.
 *
 *   Solution: monkey-patch internalModel.update() so our idle params are
 *   injected AFTER motions/expressions/physics — the last write before
 *   the drawables get rendered.
 *
 * Features:
 *  - 20 expressions + 3 motions loaded via updated model3.json
 *  - Autonomous idle: eye blink, breathing, gentle head sway (60fps)
 *  - DaiJi idle motion auto-loops on model load
 *  - Mood-driven expression + parameter presets
 *  - Talking: mouth open sine + head bob, speed varies by mood
 *  - WebSocket ↔ Python bridge (port 8765), auto-reconnect
 */

// ─────────────────────────────────────────────────────
//  CONFIGURATION
// ─────────────────────────────────────────────────────
const WS_URL = "ws://localhost:8765";
const RECONNECT_DELAY = 3000;

// ─────────────────────────────────────────────────────
//  MOOD PRESETS
//  expression : name must match model3.json Expressions[].Name
//  params     : Live2D param IDs → target value (applied after pipeline)
//  mouthSpeed : multiplier for mouth animation (1.0 = normal)
// ─────────────────────────────────────────────────────
const MOOD_PRESETS = {
    // ── Prompter moods ────────────────────────────────
    hyped: {
        expression: "星星眼",
        params: { ParamMouthForm: 0.9, ParamEyeLSmile: 0.8, ParamEyeRSmile: 0.8, ParamAngleZ: 4 },
        mouthSpeed: 1.3,
    },
    gremlin: {
        expression: "舌头",
        params: { Param52: 1.0, ParamMouthForm: 1.0, ParamAngleZ: 6 },
        mouthSpeed: 1.2,
    },
    flustered: {
        expression: "脸红",
        params: { Param31: 1.0, ParamAngleY: -6, ParamMouthForm: 0.2 },
        mouthSpeed: 1.1,
    },
    bored: {
        expression: "白眼",
        params: { ParamMouthForm: -0.4, ParamAngleZ: -4 },
        mouthSpeed: 0.85,
    },
    focused: {
        expression: "疑惑",
        params: { Param34: 0.8, ParamBrowLForm: -0.5, ParamBrowRForm: -0.5 },
        mouthSpeed: 1.0,
    },
    chill: {
        expression: null,
        params: { ParamEyeLOpen: 0.65, ParamEyeROpen: 0.65, ParamAngleZ: -2 },
        mouthSpeed: 0.9,
    },
    // ── Sentiments ────────────────────────────────────
    happy: {
        expression: "爱心眼",
        params: { ParamMouthForm: 0.7, ParamEyeLSmile: 0.6, ParamEyeRSmile: 0.6 },
        mouthSpeed: 1.1,
    },
    surprised: {
        expression: "惊讶",
        params: {
            JingYa: 1.0, ParamEyeLOpen: 1.3, ParamEyeROpen: 1.3,
            ParamBrowLAngle: 14, ParamBrowRAngle: 14
        },
        mouthSpeed: 1.15,
    },
    sad: {
        expression: "流泪",
        params: {
            Param44: 1.0, ParamMouthForm: -0.8,
            ParamBrowLAngle: -10, ParamBrowRAngle: -10, ParamAngleY: -5
        },
        mouthSpeed: 0.85,
    },
    thinking: {
        expression: "疑惑",
        params: { Param34: 0.5, ParamBrowLAngle: 5, ParamBrowRAngle: -5 },
        mouthSpeed: 0.9,
    },
    excited: {
        expression: "星星眼",
        params: { Param36: 1.0, ParamMouthForm: 1.0 },
        mouthSpeed: 1.35,
    },
    neutral: {
        expression: null,
        params: {},
        mouthSpeed: 1.0,
    },
};

// ─────────────────────────────────────────────────────
//  GLOBAL STATE
// ─────────────────────────────────────────────────────
let app = null;
let currentModel = null;
let ws = null;

// Talking
let isTalking = false;
let _mouthSpeed = 1.0;

// Timing state — advanced by ticker, read by applyIdleParams
let _phase = 0;   // global frame counter
let _mouthPhase = 0;
let _blinkCooldown = 180; // frames until next blink (~3s @ 60fps)
let _blinkProgress = -1;  // -1=not blinking, >=0=frame within blink

// Active mood param overrides (from set_mood command)
let _activeMoodParams = {};
// Temporary reaction param overrides (auto-cleared after reaction duration)
let _reactionParams = {};
let _reactMotionTimer = null;
let _reactParamTimer = null;

// ── Procedural Animation Engine ──────────────────────────────────────────────
// Animations are lists of keyframes {t:0-1, v:value} interpolated over duration.
// Multiple can run simultaneously on different params.
let _procAnims = [];          // [{id, param, keyframes, duration, elapsed}]
let _lastProcTime = performance.now();

// Dedicated wave state — explicit setP override so the hand wave is always visible
let _waveActive = false;
let _wavePhase = 0;
let _waveEndTime = 0;       // performance.now() timestamp when wave should stop

// ── Fidget system ─────────────────────────────────────────────────────
let _fidgetCooldown = 600 + Math.floor(Math.random() * 480); // first fidget 10-18s
let _fidgetTypes = [
    'lookLeft', 'lookRight', 'lookUp', 'lookDown',
    'headTilt', 'bodyShift', 'browRaise', 'quickSmile', 'eyeSquint'
];

// ── Talking head emphasis nod state ───────────────────────────────
let _talkingNodTimer  = 0;    // frames until next nod
let _talkingNodActive = false;
let _talkingNodPhase  = 0;
let _talkingNodDir    = 1;    // +1 or -1


function spawnProcAnim(id, param, keyframes, duration) {
    // Cancel any existing anim with same id, then start new one
    _procAnims = _procAnims.filter(a => a.id !== id);
    _procAnims.push({ id, param, keyframes, duration, elapsed: 0 });
}

function interpKF(kf, t) {
    // Linear search + smooth ease-in-out interpolation between keyframes
    if (t <= kf[0].t) return kf[0].v;
    if (t >= kf[kf.length - 1].t) return kf[kf.length - 1].v;
    for (let i = 0; i < kf.length - 1; i++) {
        if (t >= kf[i].t && t < kf[i + 1].t) {
            const lt = (t - kf[i].t) / (kf[i + 1].t - kf[i].t);
            const et = lt < 0.5 ? 2 * lt * lt : -1 + (4 - 2 * lt) * lt; // ease in-out
            return kf[i].v + (kf[i + 1].v - kf[i].v) * et;
        }
    }
    return kf[kf.length - 1].v;
}

function tickProcAnims(core) {
    const now = performance.now();
    const dtSec = Math.min((now - _lastProcTime) / 1000, 0.1); // cap at 100ms
    _lastProcTime = now;
    // Use ADD mode so proc anims stack ON TOP of motion3.json without destroying it
    const addP = (id, v) => {
        if (core.addParameterValueById) {
            core.addParameterValueById(id, v);
        } else {
            const cur = core.getParameterValueById ? core.getParameterValueById(id) : 0;
            core.setParameterValueById?.(id, cur + v);
        }
    };
    _procAnims = _procAnims.filter(anim => {
        anim.elapsed += dtSec;
        const t = Math.min(anim.elapsed / anim.duration, 1.0);
        addP(anim.param, interpKF(anim.keyframes, t));
        return t < 1.0;
    });
}

// Spawn compound procedural animations for a named reaction.
// Each reaction randomly varies intensity so no two look the same.
function spawnReactionProc(reactionName, durationSec) {
    const I = 1.3 + Math.random() * 0.7;   // intensity 130-200% (made much more visible)
    const d = durationSec;

    if (reactionName === 'greet') {
        // ── Activate dedicated wave oscillator (explicit setP, guaranteed visible)
        _waveActive = true;
        _wavePhase = 0;
        _waveEndTime = performance.now() + durationSec * 1000;

        // Head tilts toward raised arm, smile, slight body lean
        spawnProcAnim('r_headY', 'ParamAngleY', [
            { t: 0, v: 0 }, { t: 0.12, v: -10 * I }, { t: 0.5, v: -7 * I }, { t: 0.85, v: -5 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_headZ', 'ParamAngleZ', [
            { t: 0, v: 0 }, { t: 0.15, v: 4 * I }, { t: 0.6, v: 3 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_smile', 'ParamMouthForm', [
            { t: 0, v: 0 }, { t: 0.08, v: 0.9 * I }, { t: 0.8, v: 0.7 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_eyeSmL', 'ParamEyeLSmile', [
            { t: 0, v: 0 }, { t: 0.1, v: 0.6 * I }, { t: 0.8, v: 0.5 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_eyeSmR', 'ParamEyeRSmile', [
            { t: 0, v: 0 }, { t: 0.1, v: 0.6 * I }, { t: 0.8, v: 0.5 * I }, { t: 1, v: 0 }
        ], d);
        // Add smile + sparkle to greet
        spawnProcAnim('r_smile', 'ParamMouthSmile', [
            { t: 0, v: 0 }, { t: 0.1, v: 0.8 * I }, { t: 0.85, v: 0.6 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_eyeY', 'ParamEyeBallY', [
            { t: 0, v: 0 }, { t: 0.08, v: 0.6 * I }, { t: 0.9, v: 0.5 * I }, { t: 1, v: 0 }
        ], d);
    } else if (reactionName === 'laugh') {
        // Head bobs, eyes squint, mouth form wide smile
        const bobSpeed = 0.25 + Math.random() * 0.15;
        spawnProcAnim('r_headX', 'ParamAngleX', [
            { t: 0, v: 0 }, { t: bobSpeed, v: 5 * I }, { t: bobSpeed * 2, v: -3 * I },
            { t: bobSpeed * 3, v: 4 * I }, { t: bobSpeed * 4, v: -2 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_headZ', 'ParamAngleZ', [
            { t: 0, v: 0 }, { t: 0.15, v: -6 * I }, { t: 0.4, v: 3 * I }, { t: 0.7, v: -2 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_smile', 'ParamMouthForm', [
            { t: 0, v: 0 }, { t: 0.05, v: 1.0 }, { t: 0.85, v: 0.8 }, { t: 1, v: 0 }
        ], d);
    } else if (reactionName === 'surprised') {
        // Quick head snap back, eyes wide
        spawnProcAnim('r_headY', 'ParamAngleY', [
            { t: 0, v: 0 }, { t: 0.05, v: 8 * I }, { t: 0.2, v: 5 * I }, { t: 0.7, v: 3 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_headX', 'ParamAngleX', [
            { t: 0, v: 0 }, { t: 0.04, v: -5 * I }, { t: 0.3, v: 0 }, { t: 1, v: 0 }
        ], d);
    } else if (reactionName === 'wink') {
        spawnProcAnim('r_headZ', 'ParamAngleZ', [
            { t: 0, v: 0 }, { t: 0.1, v: -4 * I }, { t: 0.6, v: -3 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_smile', 'ParamMouthForm', [
            { t: 0, v: 0 }, { t: 0.08, v: 0.7 * I }, { t: 0.8, v: 0.5 * I }, { t: 1, v: 0 }
        ], d);
    } else if (reactionName === 'shy') {
        spawnProcAnim('r_headY', 'ParamAngleY', [
            { t: 0, v: 0 }, { t: 0.2, v: -12 * I }, { t: 0.9, v: -10 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_headX', 'ParamAngleX', [
            { t: 0, v: 0 }, { t: 0.15, v: -5 * I }, { t: 0.9, v: -4 * I }, { t: 1, v: 0 }
        ], d);
    } else if (reactionName === 'thinking') {
        spawnProcAnim('r_headZ', 'ParamAngleZ', [
            { t: 0, v: 0 }, { t: 0.3, v: -5 * I }, { t: 0.7, v: -5 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_headX', 'ParamAngleX', [
            { t: 0, v: 0 }, { t: 0.2, v: 6 * I }, { t: 0.8, v: 5 * I }, { t: 1, v: 0 }
        ], d);
    } else if (reactionName === 'sad') {
        spawnProcAnim('r_headY', 'ParamAngleY', [
            { t: 0, v: 0 }, { t: 0.3, v: -8 * I }, { t: 0.9, v: -7 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_headX', 'ParamAngleX', [
            { t: 0, v: 0 }, { t: 0.4, v: -4 * I }, { t: 0.9, v: -3 * I }, { t: 1, v: 0 }
        ], d);
    } else if (reactionName === 'smug') {
        // Cabeza inclinada, media sonrisa, ojo cerrado a medias
        spawnProcAnim('r_headZ', 'ParamAngleZ', [
            { t: 0, v: 0 }, { t: 0.2, v: -8 * I }, { t: 0.8, v: -7 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_smile', 'ParamMouthSmile', [
            { t: 0, v: 0 }, { t: 0.15, v: 0.9 * I }, { t: 0.85, v: 0.8 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_eyeL', 'ParamEyeLSmile', [
            { t: 0, v: 0 }, { t: 0.15, v: 0.7 * I }, { t: 0.85, v: 0.6 * I }, { t: 1, v: 0 }
        ], d);
    } else if (reactionName === 'disgust') {
        // Frown + ligero asco
        spawnProcAnim('r_headY', 'ParamAngleY', [
            { t: 0, v: 0 }, { t: 0.2, v: 5 * I }, { t: 0.8, v: 4 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('r_brow', 'ParamBrowLAngle', [
            { t: 0, v: 0 }, { t: 0.2, v: -0.6 * I }, { t: 0.8, v: -0.5 * I }, { t: 1, v: 0 }
        ], d);
    }

    console.log(`[Viewer] 🎭 ProcAnim: ${reactionName} I=${I.toFixed(2)} d=${d}s`);
}

// ─────────────────────────────────────────────────────
//  RANDOM FIDGET — micro-movements when idle (not talking)
//  Makes her feel alive between monologues.
// ─────────────────────────────────────────────────────
function spawnRandomFidget() {
    const type = _fidgetTypes[Math.floor(Math.random() * _fidgetTypes.length)];
    const I = 0.55 + Math.random() * 0.45;   // intensity 55-100%
    const d = 1.8 + Math.random() * 2.5;     // duration 1.8-4.3s

    if (type === 'lookLeft') {
        // Eyes + head drift left, then return
        spawnProcAnim('f_hz', 'ParamAngleZ', [
            { t: 0, v: 0 }, { t: 0.2, v: -11 * I }, { t: 0.65, v: -9 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('f_ex', 'ParamEyeBallX', [
            { t: 0, v: 0 }, { t: 0.15, v: -0.75 * I }, { t: 0.7, v: -0.55 * I }, { t: 1, v: 0 }
        ], d);
    } else if (type === 'lookRight') {
        spawnProcAnim('f_hz', 'ParamAngleZ', [
            { t: 0, v: 0 }, { t: 0.2, v: 10 * I }, { t: 0.65, v: 8 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('f_ex', 'ParamEyeBallX', [
            { t: 0, v: 0 }, { t: 0.15, v: 0.7 * I }, { t: 0.7, v: 0.5 * I }, { t: 1, v: 0 }
        ], d);
    } else if (type === 'lookUp') {
        // Look up as if remembering something
        spawnProcAnim('f_hx', 'ParamAngleX', [
            { t: 0, v: 0 }, { t: 0.25, v: 7 * I }, { t: 0.7, v: 5 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('f_ey', 'ParamEyeBallY', [
            { t: 0, v: 0 }, { t: 0.2, v: 0.55 * I }, { t: 0.7, v: 0.4 * I }, { t: 1, v: 0 }
        ], d);
    } else if (type === 'headTilt') {
        // Casual head tilt + soft smile
        spawnProcAnim('f_hz', 'ParamAngleZ', [
            { t: 0, v: 0 }, { t: 0.3, v: -8 * I }, { t: 0.75, v: -7 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('f_smile', 'ParamMouthForm', [
            { t: 0, v: 0 }, { t: 0.3, v: 0.4 * I }, { t: 0.7, v: 0.25 * I }, { t: 1, v: 0 }
        ], d);
    } else if (type === 'bodyShift') {
        // Settle into slightly different posture
        const dir = Math.random() < 0.5 ? 1 : -1;
        spawnProcAnim('f_bx', 'ParamBodyAngleX', [
            { t: 0, v: 0 }, { t: 0.35, v: dir * 7 * I }, { t: 0.75, v: dir * 5 * I }, { t: 1, v: 0 }
        ], d * 1.4);
        spawnProcAnim('f_hz', 'ParamAngleZ', [
            { t: 0, v: 0 }, { t: 0.3, v: dir * 3 * I }, { t: 0.8, v: dir * 2 * I }, { t: 1, v: 0 }
        ], d * 1.2);
    } else if (type === 'browRaise') {
        // Quick brow raise (reacting to a thought)
        spawnProcAnim('f_browL', 'ParamBrowLAngle', [
            { t: 0, v: 0 }, { t: 0.12, v: 10 * I }, { t: 0.45, v: 8 * I }, { t: 1, v: 0 }
        ], d * 0.8);
        spawnProcAnim('f_browR', 'ParamBrowRAngle', [
            { t: 0, v: 0 }, { t: 0.12, v: 10 * I }, { t: 0.45, v: 8 * I }, { t: 1, v: 0 }
        ], d * 0.8);
        spawnProcAnim('f_hx', 'ParamAngleX', [
            { t: 0, v: 0 }, { t: 0.15, v: 4 * I }, { t: 0.6, v: 3 * I }, { t: 1, v: 0 }
        ], d * 0.8);
    } else if (type === 'lookDown') {
        // Mirada hacia abajo — como cuando piensa o es tímida
        spawnProcAnim('f_hx', 'ParamAngleX', [
            { t: 0, v: 0 }, { t: 0.3, v: -6 * I }, { t: 0.7, v: -5 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('f_ey', 'ParamEyeBallY', [
            { t: 0, v: 0 }, { t: 0.25, v: -0.5 * I }, { t: 0.7, v: -0.4 * I }, { t: 1, v: 0 }
        ], d);
    } else if (type === 'quickSmile') {
        // Media sonrisa espontánea — como si recordara algo divertido
        spawnProcAnim('f_smile', 'ParamMouthForm', [
            { t: 0, v: 0 }, { t: 0.15, v: 0.6 * I }, { t: 0.6, v: 0.45 * I }, { t: 1, v: 0 }
        ], d * 0.9);
        spawnProcAnim('f_eyeSmL', 'ParamEyeLSmile', [
            { t: 0, v: 0 }, { t: 0.2, v: 0.4 * I }, { t: 0.65, v: 0.3 * I }, { t: 1, v: 0 }
        ], d * 0.9);
        spawnProcAnim('f_eyeSmR', 'ParamEyeRSmile', [
            { t: 0, v: 0 }, { t: 0.2, v: 0.4 * I }, { t: 0.65, v: 0.3 * I }, { t: 1, v: 0 }
        ], d * 0.9);
    } else if (type === 'eyeSquint') {
        // Ojo ligeramente entrecerrado — como escrutando algo
        spawnProcAnim('f_eyeL', 'ParamEyeLOpen', [
            { t: 0, v: 0 }, { t: 0.2, v: -0.25 * I }, { t: 0.7, v: -0.2 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('f_eyeR', 'ParamEyeROpen', [
            { t: 0, v: 0 }, { t: 0.2, v: -0.25 * I }, { t: 0.7, v: -0.2 * I }, { t: 1, v: 0 }
        ], d);
        spawnProcAnim('f_hz', 'ParamAngleZ', [
            { t: 0, v: 0 }, { t: 0.25, v: -4 * I }, { t: 0.75, v: -3 * I }, { t: 1, v: 0 }
        ], d);
    }
}

// DOM
const modelLabel = document.getElementById("model-label");
const wsStatus = document.getElementById("ws-status");
const noModelMsg = document.getElementById("no-model-message");

// ─────────────────────────────────────────────────────
//  PIXI SETUP
// ─────────────────────────────────────────────────────
function initPixi() {
    app = new PIXI.Application({
        autoStart: true,
        resizeTo: window,
        backgroundColor: 0x00FF00,
        backgroundAlpha: 1,
        resolution: window.devicePixelRatio || 1,
        autoDensity: true,
        antialias: true
    });
    document.getElementById("canvas-container").appendChild(app.view);
    console.log("[Viewer] PixiJS initialized.", PIXI.VERSION);
}

// ─────────────────────────────────────────────────────
//  MODEL LOADING
// ─────────────────────────────────────────────────────
async function loadModel(modelPath) {
    try {
        console.log(`[Viewer] Loading model: ${modelPath}`);

        if (currentModel) {
            app.ticker.remove(idleStateTick);
            app.stage.removeChild(currentModel);
            currentModel.destroy();
            currentModel = null;
        }

        const Live2DModel = PIXI.live2d?.Live2DModel || globalThis.PIXI?.live2d?.Live2DModel;
        if (!Live2DModel) throw new Error("pixi-live2d-display not loaded.");

        const model = await Live2DModel.from(modelPath);

        // Scale & center
        const scaleX = (app.screen.width * 0.8) / model.width;
        const scaleY = (app.screen.height * 0.85) / model.height;
        model.scale.set(Math.min(scaleX, scaleY));
        model.anchor.set(0.5, 0.5);
        model.x = app.screen.width / 2;
        model.y = app.screen.height / 2;

        // Draggable
        model.interactive = true;
        model.buttonMode = true;
        let dragging = false, dragOffset = { x: 0, y: 0 };
        model.on("pointerdown", (e) => { dragging = true; const p = e.data.global; dragOffset.x = p.x - model.x; dragOffset.y = p.y - model.y; });
        model.on("pointermove", (e) => { if (dragging) { const p = e.data.global; model.x = p.x - dragOffset.x; model.y = p.y - dragOffset.y; } });
        model.on("pointerup", () => { dragging = false; });
        model.on("pointerupoutside", () => { dragging = false; });

        app.stage.addChild(model);
        currentModel = model;

        modelLabel.textContent = modelPath.split("/").pop().replace(".model3.json", "");
        noModelMsg.style.display = "none";

        // ── Idle motion (DaiJi — Loop:true, fires once and loops) ──
        try { model.motion("Idle", 0); console.log("[Viewer] Idle motion started."); }
        catch (e) { console.warn("[Viewer] Could not start Idle motion:", e.message); }

        // ── Reset timing state ──────────────────────────────────────
        _phase = 0;
        _mouthPhase = 0;
        _blinkCooldown = 180 + Math.random() * 240;
        _blinkProgress = -1;
        _activeMoodParams = {};

        // ── Ticker: advances timing counters only (no param writes) ─
        app.ticker.add(idleStateTick);

        // ── THE KEY FIX: patch internalModel.update so our param     ─
        //    writes happen AFTER motions/expressions/physics, just     ─
        //    before the drawables are rendered.                        ─
        const _origUpdate = model.internalModel.update.bind(model.internalModel);
        model.internalModel.update = function (dt) {
            _origUpdate(dt);               // full Cubism pipeline runs
            applyIdleParams(this.coreModel); // then we override what we need
        };

        logModelCapabilities(model);
        _buildExpressionCache(model);   // ← populate fuzzy match cache
        console.log(`[Viewer] ✓ Model ready: ${modelLabel.textContent}`);

    } catch (err) {
        console.error(`[Viewer] ✗ Failed to load model: ${err.message}`);
        modelLabel.textContent = "Load Error";
        noModelMsg.style.display = "block";
    }
}

function logModelCapabilities(model) {
    try {
        const exprDefs = model.internalModel?.settings?.expressions;
        const motDefs = model.internalModel?.settings?.motions;
        if (exprDefs) console.log(`[Viewer] Expressions (${exprDefs.length}):`, exprDefs.map(e => e.Name));
        if (motDefs) console.log(`[Viewer] Motion groups:`, Object.keys(motDefs));
    } catch (e) { /* ignore */ }
}

// ─────────────────────────────────────────────────────
//  TICKER — advances timing state only, no param writes
// ─────────────────────────────────────────────────────
function idleStateTick(delta) {
    _phase += delta;
    _mouthPhase += delta;

    // Blink state machine
    _blinkCooldown -= delta;
    if (_blinkCooldown <= 0 && _blinkProgress < 0) {
        _blinkProgress = 0;
    }
    if (_blinkProgress >= 0) {
        _blinkProgress += delta;
        if (_blinkProgress >= 15) {
            _blinkProgress = -1;
            _blinkCooldown = 180 + Math.random() * 240; // next blink in 3-7s
        }
    }

    // Fidget system — only fires when not talking
    if (!isTalking) {
        _fidgetCooldown -= delta;
        if (_fidgetCooldown <= 0) {
            spawnRandomFidget();
            _fidgetCooldown = 480 + Math.floor(Math.random() * 840); // next in 8-22s
        }
    } else {
        // Talking nod system — head emphasis nods during speech
        _talkingNodTimer -= delta;
        if (_talkingNodTimer <= 0 && !_talkingNodActive) {
            _talkingNodActive = true;
            _talkingNodPhase  = 0;
            _talkingNodDir    = Math.random() < 0.6 ? 1 : -1;  // mostly forward nod
            _talkingNodTimer  = 120 + Math.floor(Math.random() * 180); // next in 2-5s
        }
        if (_talkingNodActive) {
            _talkingNodPhase += delta;
            if (_talkingNodPhase >= 40) {
                _talkingNodActive = false;
            }
        }
    }
}

// ─────────────────────────────────────────────────────
//  APPLY IDLE PARAMS — called from patched internalModel.update()
//  Runs AFTER motions / expressions / physics every frame.
//  Whatever we write here is the final value rendered.
// ─────────────────────────────────────────────────────
function applyIdleParams(core) {
    if (!core) return;
    const setP = (id, v) => core.setParameterValueById?.(id, v);

    // ── Breathing (4s cycle, 0..1) ────────────────────────────────
    setP("ParamBreath", Math.sin(_phase * Math.PI / 120) * 0.5 + 0.5);

    // ── Eye blink (15-frame animation: 5 close, 3 closed, 7 open) ─
    if (_blinkProgress >= 0) {
        const eyeVal =
            _blinkProgress < 5 ? Math.max(0, 1 - _blinkProgress / 5)   // closing
                : _blinkProgress < 8 ? 0                                       // closed
                    : Math.min(1, (_blinkProgress - 8) / 7);                       // opening
        setP("ParamEyeLOpen", eyeVal);
        setP("ParamEyeROpen", eyeVal);
    }

    // ── Mood params + reaction param overrides ────────────────────
    // _reactionParams (e.g. laugh eye-smile) wins over _activeMoodParams
    const allParams = Object.assign({}, _activeMoodParams, _reactionParams);
    for (const [id, val] of Object.entries(allParams)) {
        setP(id, val);
    }

    // Procedural animations moved to the bottom so they stack properly

    // ── Dedicated hand wave (greet) ─────────────────────────────
    // Uses setP (hard override) so the wave is always visible regardless of
    // what the HuiShou motion or SDK blending do to Param58.
    if (_waveActive) {
        const now = performance.now();
        if (now < _waveEndTime) {
            _wavePhase += 0.09;  // ~1.7 Hz wave at 60fps  — natural hand wave speed
            const remaining = Math.max(0, (_waveEndTime - now) / 1000);
            // Fade-out: full amplitude until 2s left, then taper to 0
            const fadeOut = Math.min(remaining / 2.0, 1.0);
            // Wave between -1 and +1 with fade
            setP("Param58", Math.sin(_wavePhase) * fadeOut);
        } else {
            _waveActive = false;
            setP("Param58", 0);
        }
    }

    // ── Smoothing / Lerp Helper ─────────────────────────────
    // Previene saltos bruscos al cambiar entre idle y hablando
    if (!core._lerpedParams) core._lerpedParams = {};
    const lerpP = (id, targetVal, speed = 0.08) => {
        if (core._lerpedParams[id] === undefined) {
            core._lerpedParams[id] = targetVal;
        } else {
            core._lerpedParams[id] += (targetVal - core._lerpedParams[id]) * speed;
        }
        setP(id, core._lerpedParams[id]);
    };

    // ── Talking vs idle head / body movement ──────────────────────
    if (isTalking) {
        const freq   = 32 / _mouthSpeed;
        const rawSin = Math.sin(_mouthPhase * Math.PI / freq);
        const amp    = 0.88 + Math.sin(_phase * Math.PI / 180) * 0.10;
        setP("ParamMouthOpenY", rawSin > 0 ? Math.pow(rawSin, 1.1) * amp : 0);

        // Cabeza: dos frecuencias → ritmo de habla natural
        const nod0 = Math.sin(_mouthPhase * Math.PI / 30) * 1.6;
        const nod1 = Math.sin(_mouthPhase * Math.PI / 19) * 0.7;

        // Nod de énfasis espontáneo (suavizado)
        let emphNod = 0;
        if (_talkingNodActive) {
            const t = _talkingNodPhase / 40;
            emphNod = _talkingNodDir * Math.sin(t * Math.PI) * 2.8; // Reducido para no ser tan exagerado
        }

        lerpP("ParamAngleX", (allParams.ParamAngleX || 0) + nod0 + nod1 + emphNod);
        lerpP("ParamAngleZ", (allParams.ParamAngleZ || 0) + Math.sin(_mouthPhase * Math.PI / 44) * 1.4);
        lerpP("ParamAngleY", (allParams.ParamAngleY || 0) + Math.sin(_phase * Math.PI / 580) * 2.0);

        // Ojos: contacto visual directo (miran al frente con leve drift)
        lerpP("ParamEyeBallX", Math.sin(_phase * Math.PI / 600) * 0.15);
        lerpP("ParamEyeBallY", 0.1 + Math.sin(_phase * Math.PI / 480) * 0.08);

        // Cuerpo: sigue la cabeza con ligero lag
        lerpP("ParamBodyAngleX", Math.sin(_phase * Math.PI / 520) * 2.0 + emphNod * 0.2);
        lerpP("ParamBodyAngleZ", Math.sin(_phase * Math.PI / 380) * 1.5);
        lerpP("ParamBodyAngleY", 0); // Vuelve suavemente a 0

    } else {
        // ── Idle orgánico: 3 capas de seno = movimiento tipo cámara real ──
        const swayX =
            Math.sin(_phase * Math.PI / 380) * 5.0
          + Math.sin(_phase * Math.PI / 215 + 0.8) * 1.5
          + Math.sin(_phase * Math.PI / 92  + 1.6) * 0.3;

        const swayZ =
            Math.sin(_phase * Math.PI / 260 + 1.2) * 3.5
          + Math.sin(_phase * Math.PI / 490 + 2.5) * 1.2
          + Math.sin(_phase * Math.PI / 128 + 0.4) * 0.3;

        const swayY =
            Math.sin(_phase * Math.PI / 445 + 0.3) * 4.0
          + Math.sin(_phase * Math.PI / 315 + 1.1) * 1.2;

        lerpP("ParamAngleX", (allParams.ParamAngleX || 0) + swayX);
        lerpP("ParamAngleZ", (allParams.ParamAngleZ || 0) + swayZ);
        lerpP("ParamAngleY", (allParams.ParamAngleY || 0) + swayY);

        // Cuerpo: movimiento contrario a la cabeza (inertia/peso natural)
        lerpP("ParamBodyAngleX",
            Math.sin(_phase * Math.PI / 560 + 0.7) * 3.0
          + Math.sin(_phase * Math.PI / 310 + 1.9) * 1.2);
        lerpP("ParamBodyAngleZ",
            Math.sin(_phase * Math.PI / 430 + 0.4) * 2.5
          + Math.sin(_phase * Math.PI / 235 + 2.1) * 1.0);
        lerpP("ParamBodyAngleY",
            Math.sin(_phase * Math.PI / 520 + 1.5) * 2.5);

        // Mirada: drift suave por la pantalla (mira a varios puntos)
        lerpP("ParamEyeBallX",
            Math.sin(_phase * Math.PI / 315 + 1.5) * 0.62
          + Math.sin(_phase * Math.PI / 540 + 0.8) * 0.22);
        lerpP("ParamEyeBallY",
            Math.sin(_phase * Math.PI / 445 + 0.6) * 0.42
          + Math.sin(_phase * Math.PI / 265 + 1.2) * 0.12);

        setP("ParamMouthOpenY", 0);
    }

    // ── Procedural animations (compound reactions) ───────────────────
    // Called AT THE END so it STACKS on top of idle and talking animations.
    tickProcAnims(core);

}

// ─────────────────────────────────────────────────────
//  EXPRESSION & MOOD COMMANDS
// ─────────────────────────────────────────────────────

// Cache of available expression names from the loaded model
let _modelExpressions = [];

function _buildExpressionCache(model) {
    try {
        const defs = model.internalModel?.settings?.expressions;
        if (defs) _modelExpressions = defs.map(e => e.Name || e.name || '');
    } catch(e) { _modelExpressions = []; }
}

function _fuzzyFindExpression(name) {
    if (!name) return null;
    // 1. Exact match
    if (_modelExpressions.includes(name)) return name;
    // 2. Case-insensitive match
    const lower = name.toLowerCase();
    const ci = _modelExpressions.find(e => e.toLowerCase() === lower);
    if (ci) return ci;
    // 3. Partial match (name is contained in available OR vice versa)
    const partial = _modelExpressions.find(e =>
        e.includes(name) || name.includes(e)
    );
    if (partial) return partial;
    // 4. Simplified ↔ Traditional Chinese fallback map
    const trad2simp = {
        '\u820c\u982d':'\u820c\u5934', '\u81c9\u7d05':'\u8138\u7ea2', '\u81c9\u9ed1':'\u8138\u9ed1',
        '\u6d41\u6dda':'\u6d41\u6cea', '\u751f\u6c23':'\u751f\u6c14', '\u8b8a\u614c':'\u614c\u5f20',
        '\u55dc\u8840':'\u5988\u5988', '\u6230\u9b25':'\u6218\u6597',
    };
    const simp2trad = Object.fromEntries(Object.entries(trad2simp).map(([t,s])=>[s,t]));
    const alt = trad2simp[name] || simp2trad[name];
    if (alt && _modelExpressions.includes(alt)) return alt;
    return null;
}

function setExpression(name) {
    if (!currentModel) return;
    try {
        if (!name || name === "null" || name === "none") {
            currentModel.expression(0);
            return;
        }
        const idx = parseInt(name, 10);
        if (!isNaN(idx)) {
            currentModel.expression(idx);
            console.log(`[Viewer] Expression[${idx}]`);
            return;
        }
        // Try fuzzy match first
        const resolved = _fuzzyFindExpression(name) || name;
        currentModel.expression(resolved);
        console.log(`[Viewer] Expression: ${resolved}${resolved !== name ? ' (fuzzy: '+name+')' : ''}`);
    } catch (e) {
        console.warn(`[Viewer] Expression '${name}' failed:`, e.message);
    }
}

function applyMoodPreset(mood, sentiment) {
    // Expand sentiment lookup: laughing→happy, annoyed→bored, smug→gremlin, etc.
    const sentimentAlias = {
        laughing: 'happy', annoyed: 'bored', smug: 'gremlin',
        embarrassed: 'flustered', angry: 'bored', boredom: 'bored',
    };
    const resolvedSentiment = sentimentAlias[sentiment] || sentiment;
    const preset = MOOD_PRESETS[mood] || MOOD_PRESETS[resolvedSentiment] || MOOD_PRESETS.neutral;
    setExpression(preset.expression);
    _activeMoodParams = { ...preset.params };
    _mouthSpeed = preset.mouthSpeed || 1.0;
    console.log(`[Viewer] Mood: ${mood}/${sentiment} → expr:${preset.expression} speed:${_mouthSpeed}`);
}

function playMotion(group, index = 0, priority = 2) {
    if (!currentModel) return;
    try {
        // priority 2 = NORMAL (overrides Idle), 3 = FORCE (overrides everything)
        currentModel.motion(group, index, priority);
        console.log(`[Viewer] Motion: ${group}[${index}] priority=${priority}`);
    } catch (e) {
        // Fallback without priority arg (older pixi-live2d-display versions)
        try { currentModel.motion(group, index); }
        catch (e2) { console.warn(`[Viewer] Motion '${group}' failed:`, e2.message); }
    }
}

// ─────────────────────────────────────────────────────
//  TALKING
// ─────────────────────────────────────────────────────
function startTalking(mouthSpeed) {
    if (mouthSpeed !== undefined) _mouthSpeed = mouthSpeed;
    _mouthPhase = 0;
    isTalking = true;
    console.log(`[Viewer] 🗣️ Talking (speed=${_mouthSpeed})`);
}

function stopTalking() {
    isTalking = false;
    if (currentModel) {
        const core = currentModel.internalModel?.coreModel;
        core?.setParameterValueById?.("ParamMouthOpenY", 0);
    }
    console.log("[Viewer] 🤐 Talking stopped");
}

// ─────────────────────────────────────────────────────
//  WEBSOCKET
// ─────────────────────────────────────────────────────
function connectWS() {
    console.log(`[Viewer] Connecting to ${WS_URL}...`);
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        console.log("[Viewer] ✓ WebSocket connected");
        wsStatus.textContent = "connected";
        wsStatus.className = "connected";
        ws.send(JSON.stringify({ type: "viewer_ready" }));
    };

    ws.onmessage = (event) => {
        try { handleCommand(JSON.parse(event.data)); }
        catch (e) { console.warn("[Viewer] Invalid message:", event.data); }
    };

    ws.onclose = () => {
        console.log("[Viewer] WebSocket disconnected. Reconnecting...");
        wsStatus.textContent = "disconnected";
        wsStatus.className = "disconnected";
        stopTalking();
        setTimeout(connectWS, RECONNECT_DELAY);
    };

    ws.onerror = (err) => { console.error("[Viewer] WS error:", err); ws.close(); };
}

function handleCommand(cmd) {
    console.log("[Viewer] ←", cmd.action, cmd);
    switch (cmd.action) {
        case "expression": setExpression(cmd.name || cmd.id || "0"); break;
        case "motion": playMotion(cmd.name || cmd.group || "Idle", cmd.index || 0); break;
        case "set_mood": applyMoodPreset(cmd.mood || "neutral", cmd.sentiment || "neutral"); break;
        case "talking_start": startTalking(cmd.mouth_speed); break;
        case "talking_stop": stopTalking(); break;
        case "set_param":
            if (currentModel && cmd.param) {
                currentModel.internalModel?.coreModel?.setParameterValueById?.(cmd.param, cmd.value ?? 0);
            }
            break;
        case "play_idle": playMotion("Idle", 0); break;
        case "play_meiya": playMotion("MeiYan", 0); break;
        case "play_huishou": playMotion("HuiShou", 0); break;

        // ── Timed reaction: motion + param pulse, then back to Idle ─
        case "react": {
            const rGroup = cmd.motion_group;
            const rDuration = cmd.duration_ms || 3500;
            const rParams = cmd.params || {};

            // Cancel any in-flight reaction timers
            if (_reactMotionTimer) { clearTimeout(_reactMotionTimer); _reactMotionTimer = null; }
            if (_reactParamTimer) { clearTimeout(_reactParamTimer); _reactParamTimer = null; }

            // Play motion with FORCE priority (3) so it always overrides Idle
            // Spawn compound procedural animation (head, smile, etc.)
            const rName = cmd.reaction_name || rGroup || '';
            spawnReactionProc(rName.toLowerCase(), (cmd.duration_ms || 3500) / 1000);

            if (rGroup) {
                playMotion(rGroup, 0, 3);

                // Return to Idle after the reaction completes
                _reactMotionTimer = setTimeout(() => {
                    playMotion("Idle", 0, 1); // priority 1 = IDLE (lowest, won't interrupt)
                    _reactMotionTimer = null;
                    console.log(`[Viewer] ↩ Returning to Idle after ${rGroup}`);
                }, rDuration + 300);
            }

            // Apply temporary param overrides, clear after duration
            if (Object.keys(rParams).length > 0) {
                _reactionParams = { ...rParams };
                _reactParamTimer = setTimeout(() => {
                    // Step 1: gradually fade override params toward 0
                    const clearPose = {};
                    for (const key of Object.keys(rParams)) clearPose[key] = 0;
                    _reactionParams = clearPose;
                    // Step 2: fully remove after 500ms of zero-frame rendering
                    setTimeout(() => {
                        _reactionParams = {};
                        _reactParamTimer = null;
                    }, 500);
                }, rDuration);
            }

            console.log(`[Viewer] ⚡ React: ${rGroup || '(params-only)'} for ${rDuration}ms`, rParams);
            break;
        }
        case "load_model": if (cmd.path) loadModel(cmd.path); break;
        case "set_background":
            if (cmd.color && app)
                app.renderer.background.color = parseInt(cmd.color.replace("#", ""), 16);
            break;
        // ── Real-time lip sync via RMS volume from Python TTS ────────
        case "mouth_volume": {
            if (isTalking && currentModel) {
                const rms = Math.min(cmd.value || 0, 1.0);
                // Map RMS (0-1) to mouth open (0-0.95) with slight curve
                const mouthVal = Math.pow(rms, 0.6) * 0.95;
                const core = currentModel.internalModel?.coreModel;
                core?.setParameterValueById?.("ParamMouthOpenY", mouthVal);
            }
            break;
        }
        // ── Caption overlay ─────────────────────────────────────────
        case "caption":
            // Handled by captions.html — ignore silently here
            break;
        default: console.warn(`[Viewer] Unknown action: ${cmd.action}`);
    }
}

// ─────────────────────────────────────────────────────
//  RESIZE
// ─────────────────────────────────────────────────────
window.addEventListener("resize", () => {
    if (currentModel && app) {
        const rawW = currentModel.width / currentModel.scale.x;
        const rawH = currentModel.height / currentModel.scale.y;
        const scale = Math.min((app.screen.width * 0.8) / rawW, (app.screen.height * 0.85) / rawH);
        currentModel.scale.set(scale);
        currentModel.x = app.screen.width / 2;
        currentModel.y = app.screen.height / 2;
    }
});

// ─────────────────────────────────────────────────────
//  INIT
// ─────────────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", () => {
    console.log("[Viewer] Starting Live2D Viewer...");
    initPixi();
    noModelMsg.style.display = "block";
    modelLabel.textContent = "waiting...";
    connectWS();
});
