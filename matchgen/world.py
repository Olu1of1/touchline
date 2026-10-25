"""The fictional league: clubs, playing styles, formations, squads, referees.

Every name here is invented. Club ratings are relative multipliers on expected
goals for (attack) and against (defence) versus an average side; their spread
mirrors the gap between top and bottom sides in a modern top-flight league.
"""

import math
import random

LEAGUE = {
    "league_id": "SYN1",
    "name": "Synthetic Football League",
    "season": "2026/27",
    "country": "Fictional",
    "clubs": 20,
    "matches": 380,
    "note": "All clubs, players, referees and matches are fictional and generated.",
}

# style knobs ------------------------------------------------------------------
# short_bias: share of non-cross passes that are short
# fwd: mean forward progression (m) of a short pass
# long_ball: probability of a long ball from own half
# width: propensity to cross from wide areas
# dribble: propensity to take players on / carry
# press: pressing intensity (raises opponent errors in their own half)
# counter: extra directness after winning the ball
# tempo: seconds-per-action multiplier (lower = quicker)
STYLES = {
    "possession": dict(short_bias=0.94, fwd=2.5, long_ball=0.05, width=0.35, dribble=0.20, press=0.55, counter=0.2, tempo=1.00),
    "high_press": dict(short_bias=0.88, fwd=4.0, long_ball=0.10, width=0.40, dribble=0.22, press=0.95, counter=0.5, tempo=0.90),
    "counter":    dict(short_bias=0.80, fwd=6.0, long_ball=0.18, width=0.35, dribble=0.28, press=0.35, counter=1.0, tempo=0.85),
    "direct":     dict(short_bias=0.70, fwd=6.5, long_ball=0.35, width=0.60, dribble=0.15, press=0.45, counter=0.6, tempo=0.90),
    "wing_play":  dict(short_bias=0.85, fwd=4.0, long_ball=0.12, width=0.85, dribble=0.30, press=0.50, counter=0.4, tempo=0.95),
    "balanced":   dict(short_bias=0.85, fwd=4.0, long_ball=0.14, width=0.45, dribble=0.22, press=0.55, counter=0.5, tempo=0.95),
}

# formations: (role, home x, home y) in attacking perspective (y=0 right touchline)
FORMATIONS = {
    "4-3-3": [("GK", 5, 34), ("RB", 33, 9), ("RCB", 22, 25), ("LCB", 22, 43), ("LB", 33, 59),
              ("DM", 42, 34), ("RCM", 55, 23), ("LCM", 55, 45), ("RW", 80, 10), ("ST", 88, 34), ("LW", 80, 58)],
    "4-2-3-1": [("GK", 5, 34), ("RB", 33, 9), ("RCB", 22, 25), ("LCB", 22, 43), ("LB", 33, 59),
                ("RDM", 45, 26), ("LDM", 45, 42), ("RAM", 70, 13), ("CAM", 70, 34), ("LAM", 70, 55), ("ST", 88, 34)],
    "4-4-2": [("GK", 5, 34), ("RB", 33, 9), ("RCB", 22, 25), ("LCB", 22, 43), ("LB", 33, 59),
              ("RM", 60, 10), ("RCM", 50, 27), ("LCM", 50, 41), ("LM", 60, 58), ("RST", 86, 28), ("LST", 86, 40)],
    "3-4-3": [("GK", 5, 34), ("RCB", 24, 18), ("CB", 20, 34), ("LCB", 24, 50), ("RWB", 55, 6),
              ("RCM", 50, 27), ("LCM", 50, 41), ("LWB", 55, 62), ("RW", 78, 15), ("ST", 88, 34), ("LW", 78, 53)],
    "5-3-2": [("GK", 5, 34), ("RWB", 45, 6), ("RCB", 22, 21), ("CB", 18, 34), ("LCB", 22, 47), ("LWB", 45, 62),
              ("DM", 42, 34), ("RCM", 55, 24), ("LCM", 55, 44), ("RST", 85, 28), ("LST", 85, 40)],
}

ROLE_GROUP = {
    "GK": "GK",
    "RB": "DEF", "LB": "DEF", "RCB": "DEF", "LCB": "DEF", "CB": "DEF", "RWB": "DEF", "LWB": "DEF",
    "DM": "MID", "RDM": "MID", "LDM": "MID", "RCM": "MID", "LCM": "MID", "RM": "MID", "LM": "MID",
    "CAM": "MID", "RAM": "MID", "LAM": "MID",
    "RW": "FWD", "LW": "FWD", "ST": "FWD", "RST": "FWD", "LST": "FWD",
}

# weight of each role as a shooter (open play) and as a creator (final pass)
SHOOT_W = {"GK": 0.0, "DEF": 0.35, "MID": 0.8, "FWD": 2.6}
SHOOT_ROLE_BONUS = {"ST": 1.4, "RST": 1.2, "LST": 1.2, "CAM": 1.3, "RW": 1.1, "LW": 1.1, "RAM": 1.1, "LAM": 1.1}
CREATE_W = {"GK": 0.0, "DEF": 0.5, "MID": 1.4, "FWD": 1.2}
CREATE_ROLE_BONUS = {"CAM": 1.6, "RW": 1.3, "LW": 1.3, "RAM": 1.3, "LAM": 1.3, "RB": 1.1, "LB": 1.1,
                     "RWB": 1.4, "LWB": 1.4, "RM": 1.3, "LM": 1.3}

# club table --------------------------------------------------------------------
# attack / defence are xG-for and xG-against multipliers relative to an average side.
CLUBS = [
    # id, name, short, code, city, stadium, capacity, style, formation, attack, defence, colours
    ("COR", "Corriemoor City", "Corriemoor", "COR", "Corriemoor", "The Citadel", 53400, "possession", "4-3-3", 1.50, 0.66, ("#6CABDD", "#FFFFFF")),
    ("RAV", "Ravensholt Athletic", "Ravensholt", "RAV", "Ravensholt", "Ravensholt Park", 60200, "high_press", "4-3-3", 1.42, 0.70, ("#B3121B", "#111111")),
    ("DUN", "Dunhallow United", "Dunhallow", "DUN", "Dunhallow", "Old Quarry Road", 61800, "counter", "4-2-3-1", 1.30, 0.78, ("#1C2C5B", "#F2C230")),
    ("NGH", "Northgate Harriers", "Northgate", "NGH", "Northgate", "Harrier Lane", 41500, "balanced", "4-2-3-1", 1.25, 0.80, ("#0B6E4F", "#FFFFFF")),
    ("GLE", "Glenmarrow", "Glenmarrow", "GLE", "Glenmarrow", "The Marrow", 36100, "high_press", "4-2-3-1", 1.15, 0.88, ("#5B2C83", "#E8E8E8")),
    ("KES", "Kestrel Bay", "Kestrel Bay", "KES", "Kestrel Bay", "Cliffside Stadium", 32800, "wing_play", "4-3-3", 1.12, 0.92, ("#00A3E0", "#0A2240")),
    ("QUE", "Queensreach Rangers", "Queensreach", "QUE", "Queensreach", "Crown Meadow", 38700, "wing_play", "4-3-3", 1.05, 0.98, ("#003DA5", "#FFFFFF")),
    ("HAR", "Hartsmere Vale", "Hartsmere", "HAR", "Hartsmere", "Vale Park Road", 27400, "possession", "4-3-3", 1.00, 0.98, ("#C8102E", "#FFFFFF")),
    ("OAK", "Oakhurst Sporting", "Oakhurst", "OAK", "Oakhurst", "The Acorn", 30900, "possession", "3-4-3", 0.98, 1.02, ("#2E7D32", "#FDD835")),
    ("WYV", "Wyvern Docks", "Wyvern", "WYV", "Wyvern", "Dockside Ground", 25600, "high_press", "3-4-3", 0.97, 1.00, ("#E65100", "#1A1A1A")),
    ("ASH", "Ashcombe Rovers", "Ashcombe", "ASH", "Ashcombe", "Combe Lane", 31200, "balanced", "4-4-2", 0.95, 0.98, ("#1565C0", "#FFFFFF")),
    ("LAR", "Larkspur County", "Larkspur", "LAR", "Larkspur", "County Ground", 28300, "wing_play", "4-4-2", 0.94, 1.05, ("#7B1FA2", "#FFD54F")),
    ("ESK", "Eskmouth Wanderers", "Eskmouth", "ESK", "Eskmouth", "Estuary Park", 26100, "counter", "5-3-2", 0.90, 1.00, ("#212121", "#F5F5F5")),
    ("BRB", "Brackenford Borough", "Brackenford", "BRB", "Brackenford", "Bracken Hill", 24900, "direct", "4-4-2", 0.90, 1.08, ("#8D6E63", "#FFFFFF")),
    ("PEN", "Pennwick United", "Pennwick", "PEN", "Pennwick", "St Aldric's Field", 23800, "balanced", "4-2-3-1", 0.86, 1.10, ("#D32F2F", "#1E88E5")),
    ("THI", "Thistlecombe", "Thistlecombe", "THI", "Thistlecombe", "The Thistle", 21500, "balanced", "5-3-2", 0.80, 1.02, ("#4A148C", "#C0CA33")),
    ("MIL", "Millbeck Forest", "Millbeck", "MIL", "Millbeck", "Millstream Road", 22700, "counter", "4-4-2", 0.82, 1.15, ("#1B5E20", "#FFFFFF")),
    ("SAL", "Saltmarsh Town", "Saltmarsh", "SAL", "Saltmarsh", "Marsh End", 20400, "direct", "4-4-2", 0.80, 1.20, ("#FBC02D", "#0D47A1")),
    ("IRO", "Ironmere Town", "Ironmere", "IRO", "Ironmere", "The Foundry", 19800, "direct", "4-4-2", 0.78, 1.22, ("#455A64", "#FF7043")),
    ("FER", "Fernhill FC", "Fernhill", "FER", "Fernhill", "Fern Lane", 18600, "counter", "5-3-2", 0.75, 1.20, ("#00695C", "#FFFFFF")),
]

FIRST_NAMES = {
    "England": ["Callum", "Harvey", "Reece", "Toby", "Ollie", "Jack", "Mason", "Ellis", "Freddie", "Kieran", "Rhys", "Tom",
                "Joe", "Ryan", "Lewis", "Ben", "Sam", "Alfie", "Morgan", "Jamie", "Dan", "Will", "Charlie", "Luke"],
    "Nigeria": ["Chidi", "Tobi", "Femi", "Emeka", "Kunle", "Segun", "Obinna", "Ayo", "Ifeanyi", "Dapo", "Uche", "Kelechi"],
    "Ghana": ["Kwame", "Kofi", "Yaw", "Kwabena", "Kojo", "Nana", "Ebo", "Fiifi"],
    "Senegal": ["Moussa", "Ousmane", "Cheikh", "Mamadou", "Ibrahima", "Pape", "Abdou", "Lamine"],
    "France": ["Théo", "Hugo", "Lucas", "Maxime", "Bastien", "Adrien", "Romain", "Quentin", "Yanis", "Enzo"],
    "Spain": ["Álvaro", "Iker", "Javier", "Pablo", "Unai", "Rubén", "Adrián", "Marcos", "Sergio", "Iván"],
    "Portugal": ["Tiago", "Rúben", "Gonçalo", "Diogo", "Nuno", "Vitinha", "Rafael", "Duarte"],
    "Brazil": ["Thiago", "Caio", "Matheus", "Wesley", "Igor", "Danilo", "Felipe", "Murilo", "Renan"],
    "Netherlands": ["Daan", "Jesse", "Bram", "Sem", "Joost", "Teun", "Ruben", "Milan"],
    "Norway": ["Sindre", "Eirik", "Magnus", "Håkon", "Sander", "Jonas", "Vegard"],
    "Germany": ["Lukas", "Jannik", "Florian", "Niklas", "Tobias", "Felix", "Moritz"],
    "Argentina": ["Facundo", "Nahuel", "Lautaro", "Gonzalo", "Franco", "Agustín", "Tomás"],
    "Japan": ["Haruto", "Ren", "Sota", "Yuto", "Kaito", "Riku"],
    "Ireland": ["Cian", "Darragh", "Oisín", "Conor", "Seán", "Fionn"],
    "Scotland": ["Fraser", "Ewan", "Calum", "Ross", "Euan", "Gregor"],
    "Wales": ["Gethin", "Rhodri", "Iwan", "Dylan", "Owain"],
    "Côte d'Ivoire": ["Serge", "Yao", "Didier", "Hamed", "Wilfried"],
    "Denmark": ["Mikkel", "Rasmus", "Kasper", "Andreas", "Mads"],
}
LAST_NAMES = {
    "England": ["Ashworth", "Brindley", "Coleby", "Darrow", "Elsworth", "Fairclough", "Garside", "Halloway", "Ingham",
                "Kettering", "Lomax", "Marsden", "Nettleton", "Oakes", "Pemberton", "Quayle", "Ridley", "Stanforth",
                "Thursby", "Upton", "Varley", "Whitlock", "Yardley", "Brereton", "Calloway", "Denholm", "Furness",
                "Gaskell", "Hebden", "Langtry", "Mellor", "Pickering", "Rushworth", "Sowerby", "Tolland", "Wainwright"],
    "Nigeria": ["Adeyemi", "Okonkwo", "Balogun", "Eze", "Afolabi", "Nwankwo", "Ogunleye", "Chukwu", "Adebayo", "Okafor",
                "Oyelaran", "Ikechukwu", "Akintola", "Obi"],
    "Ghana": ["Mensah", "Asante", "Boateng", "Owusu", "Appiah", "Agyeman", "Darko", "Ofori"],
    "Senegal": ["Ndiaye", "Diallo", "Sarr", "Faye", "Gueye", "Sow", "Cissé", "Diouf"],
    "France": ["Lemaire", "Garnier", "Rousseau", "Fontaine", "Chevalier", "Marchand", "Bertrand", "Dufour", "Perrin"],
    "Spain": ["Gallardo", "Ibarra", "Montero", "Navarro", "Ortega", "Salvatierra", "Varela", "Cabrera", "Echeverri"],
    "Portugal": ["Carvalhal", "Figueiredo", "Magalhães", "Pinheiro", "Quaresmo", "Teixeira", "Valente"],
    "Brazil": ["Albuquerque", "Bezerra", "Cavalcanti", "Damasceno", "Fagundes", "Medeiros", "Sampaio"],
    "Netherlands": ["van Dalen", "de Groot", "Verbeek", "Hoekstra", "Brouwer", "van Leeuwen", "Bakker"],
    "Norway": ["Lunde", "Haugen", "Strand", "Bakke", "Solberg", "Nygård"],
    "Germany": ["Brandt", "Kessler", "Lorenz", "Vogt", "Hartmann", "Seidel"],
    "Argentina": ["Benítez", "Coria", "Ledesma", "Maidana", "Quiroga", "Villalba"],
    "Japan": ["Tanabe", "Morioka", "Kurosawa", "Hayakawa", "Nishida", "Ozaki"],
    "Ireland": ["Brennan", "Gallagher", "Kinsella", "Mulcahy", "Ó Ceallaigh", "Tierney"],
    "Scotland": ["McAlister", "Drummond", "Kinnear", "Strachan", "Buchanan", "Lennox"],
    "Wales": ["Pritchard", "Llewellyn", "Vaughan", "Prosser", "Gwilym"],
    "Côte d'Ivoire": ["Kouassi", "Bamba", "Koné", "Yapi", "Gnagnon"],
    "Denmark": ["Kjær", "Lindholm", "Thorsen", "Dalgaard", "Mortensen"],
}
NATION_WEIGHTS = {"England": 30, "Nigeria": 6, "Ghana": 4, "Senegal": 4, "France": 7, "Spain": 6, "Portugal": 5,
                  "Brazil": 6, "Netherlands": 5, "Norway": 3, "Germany": 4, "Argentina": 4, "Japan": 2, "Ireland": 4,
                  "Scotland": 4, "Wales": 3, "Côte d'Ivoire": 3, "Denmark": 3}

REFEREE_NAMES = ["Martin Hollis", "Gareth Pendle", "Simon Arkwright", "Neil Castleford", "Rebecca Thorne",
                 "David Okoro", "Paul Whitmore", "Andrew Sallis", "Chris Lavery", "Imran Shafiq",
                 "Graham Tulley", "Kate Brannigan", "Lee Fothergill", "Mark Ellery"]


def club_records(spread=1.0):
    """Clubs with normalised effective ratings. `spread` (>=0) stretches or squeezes the gap between sides."""
    att = [c[9] ** spread for c in CLUBS]
    dfn = [c[10] ** spread for c in CLUBS]
    ga = math.exp(sum(math.log(a) for a in att) / len(att))
    gd = math.exp(sum(math.log(d) for d in dfn) / len(dfn))
    out = []
    for c, a, d in zip(CLUBS, att, dfn):
        out.append({
            "club_id": c[0], "name": c[1], "short_name": c[2], "code": c[3], "city": c[4], "stadium": c[5],
            "capacity": c[6], "style": c[7], "formation": c[8], "primary_colour": c[11][0],
            "secondary_colour": c[11][1], "attack_rating": round(a / ga, 4), "defence_rating": round(d / gd, 4),
        })
    return out


def _pick_nation(rng):
    nations = list(NATION_WEIGHTS)
    return rng.choices(nations, weights=[NATION_WEIGHTS[n] for n in nations])[0]


def build_squads(clubs, seed=2026):
    """25-man squads: a starter and a backup for every formation slot, a third keeper, plus two utility players."""
    rng = random.Random(seed)
    used = set()
    players = []
    pid = 1
    for club in clubs:
        quality = (club["attack_rating"] / club["defence_rating"]) ** 0.5   # >1 for strong sides
        slots = FORMATIONS[club["formation"]]
        squad_roles = []
        for role, _, _ in slots:
            squad_roles.append((role, True))
            squad_roles.append((role, False))
        squad_roles.append(("GK", False))
        squad_roles.append(("CM" if "RCM" in [s[0] for s in slots] else "DM", False))
        squad_roles.append(("ST", False))
        numbers = list(range(2, 40))
        rng.shuffle(numbers)
        for role, starter in squad_roles:
            group = ROLE_GROUP.get(role, "MID")
            while True:
                nat = _pick_nation(rng)
                name = f"{rng.choice(FIRST_NAMES[nat])} {rng.choice(LAST_NAMES[nat])}"
                if name not in used:
                    used.add(name)
                    break
            base = 70 + 9 * math.log(quality) / math.log(1.5) + (4 if starter else -3)
            overall = int(round(min(92, max(55, rng.gauss(base, 3.5)))))
            age = int(round(min(37, max(17, rng.gauss(26.5 if starter else 24.5, 4)))))

            def attr(mu):
                return int(round(min(95, max(30, rng.gauss(mu, 7)))))

            fin_mu = {"FWD": overall + 2, "MID": overall - 8, "DEF": overall - 22, "GK": 25}[group]
            pas_mu = {"FWD": overall - 4, "MID": overall + 2, "DEF": overall - 5, "GK": overall - 20}[group]
            drb_mu = {"FWD": overall + 1, "MID": overall - 3, "DEF": overall - 12, "GK": 25}[group]
            dfn_mu = {"FWD": overall - 30, "MID": overall - 6, "DEF": overall + 2, "GK": 30}[group]
            players.append({
                "player_id": f"P{pid:04d}",
                "club_id": club["club_id"],
                "name": name,
                "short_name": name.split(" ", 1)[1],
                "shirt_number": 1 if (role == "GK" and starter) else (13 if role == "GK" and not starter and
                                                                       not any(p["shirt_number"] == 13 and p["club_id"] == club["club_id"] for p in players)
                                                                       else numbers.pop()),
                "position_group": group,
                "primary_role": role if role != "CM" else "RCM",
                "nationality": nat,
                "age": age,
                "preferred_foot": rng.choices(["right", "left", "both"], weights=[70, 26, 4])[0],
                "height_cm": int(round(rng.gauss(190 if group == "GK" else 182 if group == "DEF" else 178, 5))),
                "overall": overall,
                "finishing": attr(fin_mu),
                "passing": attr(pas_mu),
                "dribbling": attr(drb_mu),
                "defending": attr(dfn_mu),
                "aerial": attr(overall - 5 if group in ("DEF", "FWD") else overall - 15),
                "shot_stopping": attr(overall + 2) if group == "GK" else None,
                "discipline": attr(70),
                "first_choice": starter,
            })
            pid += 1
    return players
