import random
from datetime import datetime
from groq import Groq

class GachaSimulator:
    """
    Aiko pulls gacha with real anime characters.
    Chat votes on which banner to pull.
    Pity system included.
    """
    
    BANNERS = {
        "attack_on_titan": {
            "name": "Attack on Titan",
            "characters": {
                "SSR": ["Eren Yeager (Titán)", "Levi Ackerman", "Historia Reiss"],
                "SR":  ["Mikasa Ackerman", "Armin Arlert", "Hange Zoë",
                        "Erwin Smith"],
                "R":   ["Connie Springer", "Sasha Blouse", "Jean Kirstein",
                        "Reiner Braun", "Bertholdt Hoover"],
            }
        },
        "demon_slayer": {
            "name": "Demon Slayer",
            "characters": {
                "SSR": ["Tanjiro Kamado", "Rengoku Kyojuro", "Yoriichi Tsugikuni"],
                "SR":  ["Nezuko Kamado", "Inosuke Hashibira", "Zenitsu Agatsuma",
                        "Shinobu Kocho"],
                "R":   ["Kanao Tsuyuri", "Tengen Uzui", "Muichiro Tokito",
                        "Gyomei Himejima"],
            }
        },
        "jujutsu_kaisen": {
            "name": "Jujutsu Kaisen",
            "characters": {
                "SSR": ["Gojo Satoru", "Ryomen Sukuna", "Yuta Okkotsu"],
                "SR":  ["Yuji Itadori", "Megumi Fushiguro", "Nobara Kugisaki",
                        "Nanami Kento"],
                "R":   ["Toge Inumaki", "Maki Zenin", "Panda",
                        "Aoi Todo"],
            }
        },
        "my_hero_academia": {
            "name": "My Hero Academia",
            "characters": {
                "SSR": ["All Might", "Izuku Midoriya (100%)", "Shigaraki Tomura"],
                "SR":  ["Katsuki Bakugo", "Shoto Todoroki", "Ochaco Uraraka",
                        "Tenya Iida"],
                "R":   ["Eijiro Kirishima", "Denki Kaminari", "Tsuyu Asui",
                        "Fumikage Tokoyami"],
            }
        },
        "genshin_impact": {
            "name": "Genshin Impact",
            "characters": {
                "SSR": ["Hu Tao", "Raiden Shogun", "Nahida", "Furina",
                        "Zhongli", "Venti"],
                "SR":  ["Xiangling", "Fischl", "Beidou", "Ningguang",
                        "Bennett", "Xingqiu"],
                "R":   ["Amber", "Kaeya", "Lisa", "Noelle",
                        "Barbara", "Razor"],
            }
        },
    }
    
    # Pity rates
    SSR_BASE_RATE = 0.03    # 3% base
    SSR_SOFT_PITY = 74      # soft pity starts here  
    SSR_HARD_PITY = 90      # guaranteed SSR
    SR_RATE = 0.15          # 15% SR chance

    def __init__(self, memory_engine, identity, groq_api_key: str):
        self.memory = memory_engine
        self.identity = identity
        self.groq = Groq(api_key=groq_api_key)
        self.pity_counter = 0
        self.current_banner = "genshin_impact"
        self.active = False
        self.vote_counts = {}
        self.voting_active = False

    def start_banner_vote(self) -> str:
        """Start a vote for which banner to pull"""
        self.vote_counts = {k: 0 for k in self.BANNERS.keys()}
        self.voting_active = True
        
        banner_list = "\n".join([
            f"  {i+1}. {v['name']}" 
            for i, (k, v) in enumerate(self.BANNERS.items())
        ])
        return (f"voten qué banner quieren que jale — "
                f"escriban el número:\n{banner_list}\n"
                f"tienen 30 segundos")

    def register_vote(self, username: str, vote: str):
        """Register a chat vote"""
        if not self.voting_active:
            return
        try:
            idx = int(vote.strip()) - 1
            banner_key = list(self.BANNERS.keys())[idx]
            self.vote_counts[banner_key] = \
                self.vote_counts.get(banner_key, 0) + 1
        except:
            pass

    def end_vote(self) -> str:
        """End voting and set winning banner"""
        self.voting_active = False
        if not self.vote_counts or all(
                v == 0 for v in self.vote_counts.values()):
            return self.current_banner
        winner = max(self.vote_counts, key=self.vote_counts.get)
        self.current_banner = winner
        return winner

    def pull(self, voter: str = None) -> dict:
        """Execute a single pull"""
        self.pity_counter += 1
        banner = self.BANNERS[self.current_banner]
        
        # Calculate SSR rate with pity
        ssr_rate = self.SSR_BASE_RATE
        if self.pity_counter >= self.SSR_SOFT_PITY:
            extra = (self.pity_counter - self.SSR_SOFT_PITY) * 0.06
            ssr_rate = min(1.0, self.SSR_BASE_RATE + extra)
        if self.pity_counter >= self.SSR_HARD_PITY:
            ssr_rate = 1.0
        
        # Determine rarity
        roll = random.random()
        if roll < ssr_rate:
            rarity = "SSR"
            self.pity_counter = 0
        elif roll < ssr_rate + self.SR_RATE:
            rarity = "SR"
        else:
            rarity = "R"
        
        character = random.choice(banner["characters"][rarity])
        
        # Generate Aiko's reaction
        reaction = self._generate_reaction(
            character, rarity, self.pity_counter, banner["name"])
        
        # Save to memory
        self.memory.save_gacha_pull(
            banner=self.current_banner,
            character=character,
            rarity=rarity,
            pity=self.pity_counter,
            reaction=reaction,
            voter=voter
        )
        self.identity.evolve_from_event("gacha_pull", {
            "character": character, "rarity": rarity})
        
        return {
            "character": character,
            "rarity": rarity,
            "pity": self.pity_counter + (0 if rarity != "SSR" else -1),
            "reaction": reaction,
            "banner": banner["name"]
        }

    def pull_ten(self, voter: str = None) -> list:
        """Execute 10 pulls at once"""
        results = []
        for _ in range(10):
            results.append(self.pull(voter))
        return results

    def _generate_reaction(self, character: str, rarity: str, 
                           pity: int, banner: str) -> str:
        mood = self.identity.get_current_mood()
        
        context_map = {
            "SSR": f"¡¡¡SAQUÉ {character} SSR!!! después de {pity} pulls",
            "SR":  f"SR... {character}. En el banner de {banner}.",
            "R":   f"R otra vez. {character}. Que decepción.",
        }
        
        prompt = f"""Eres Aiko y acabas de sacar en gacha: {context_map[rarity]}
Tu humor: {mood}

Reacciona en UNA oración, como Aiko lo haría:
- SSR: puedes gritar, llorar de emoción, presumir
- SR: decepción media, resignación, o sorpresa si era el que querías  
- R: drama total o indiferencia absoluta
Informal, en español, sin explicar el contexto."""

        try:
            response = self.groq.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=50,
                temperature=1.0
            )
            return response.choices[0].message.content.strip()
        except:
            defaults = {
                "SSR": f"¡¡¡{character} SSR!!! NO PUEDO CREERLO",
                "SR": f"{character} SR... bueno, algo es algo",
                "R": f"{character} R... next.",
            }
            return defaults[rarity]

    def get_stats_summary(self) -> str:
        cursor = self.memory.conn.execute("""
            SELECT rarity, COUNT(*) as count 
            FROM gacha_history 
            GROUP BY rarity
        """)
        rows = cursor.fetchall()
        if not rows:
            return "sin historial de gacha todavía"
        stats = {r[0]: r[1] for r in rows}
        total = sum(stats.values())
        ssrs = stats.get("SSR", 0)
        return (f"total: {total} pulls, "
                f"{ssrs} SSRs ({ssrs/total*100:.1f}% tasa), "
                f"pity actual: {self.pity_counter}")
