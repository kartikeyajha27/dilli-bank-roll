# app.py
import html
import random
import threading
import time
import uuid
import secrets

import json

import streamlit as st
import streamlit.components.v1 as components

# ---------------- settings ----------------
MAX_PLAYERS = 4
START_CASH = 15_000
SALARY = 2_000          # for passing START
WIN_NETWORTH = 30_000   # first to this net worth wins
MAX_ROUNDS = 40         # otherwise richest after this many rounds wins
TAX = 1_000
FINE = 500
BANK = "BANK"
STEP = 0.22             # seconds per tile hop in the movement animation

PCOL = ["#2f7de1", "#4cb050", "#e5473d", "#f2c230"]   # blue, green, red, yellow
OWNABLE = {"prop", "metro", "airport", "utility"}
DIE = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}

BR, GR, TE, OR = "#a0623b", "#7aa84a", "#3c8f6d", "#e8a13a"
PK, BL, PU, RD, GD = "#a8456b", "#4a90d9", "#7a4fb5", "#e4623b", "#d9b13b"


def prop(name, price, rent, color):
    return {"name": name, "kind": "prop", "price": price, "rent": rent, "color": color}


def special(name, kind, icon):
    return {"name": name, "kind": kind, "icon": icon}


def utility(name, price, rent, icon):
    """Purchasable utility tile; utilities need the same price/rent fields as other assets."""
    return {"name": name, "kind": "utility", "price": price, "rent": rent, "icon": icon}


# 28-tile Delhi board. Side order follows the clockwise perimeter layout.
BOARD = [
    special("Start", "start", "⬆️"),
    # Left row (bottom to top): green set, Blue Line metro separator, orange/pink set.
    prop("Dwarka", 1400, 160, GR),
    prop("Rohini", 1200, 140, GR),
    prop("Model Town", 1500, 180, GR),
    {"name": "Blue Line Metro", "kind": "metro", "price": 2000, "rent": 250, "icon": "🚇"},
    prop("Mayur Vihar", 1100, 120, BR),
    prop("Chandni Chowk", 1800, 220, BR),
    special("Auction", "auction", "🔨"),
    # Top row (left to right): pink set grouped together; Tirri is the utility separator.
    prop("Saket", 2400, 300, PK),
    prop("Lajpat Nagar", 2200, 280, PK),
    utility("Tirri Utility", 1900, 230, "🛺"),
    prop("Punjabi Bagh", 2100, 260, OR),
    prop("Nehru Place", 2000, 250, OR),
    prop("Sarojini Nagar", 1300, 150, OR),
    special("India Gate", "park", "🕌"),
    # Right row (top to bottom): purple set, Yellow Line metro separator, red set.
    prop("Vasant Kunj", 2800, 350, PU),
    prop("Hauz Khas", 2600, 320, PU),
    prop("Green Park", 2700, 330, PU),
    {"name": "Yellow Line Metro", "kind": "metro", "price": 2000, "rent": 250, "icon": "🚇"},
    prop("Connaught Place (CP)", 3000, 400, RD),
    prop("Khan Market", 2900, 370, RD),
    special("Police Naka", "naka", "🚔"),
    # Bottom row is rendered right-to-left by index: Police Naka, Blue Tokai,
    # Panchsheel + Greater Kailash (gold), IGI, Defence Colony + Golf Links (teal).
    prop("Golf Links", 3300, 430, TE),
    prop("Defence Colony", 3200, 420, TE),
    utility("IGI", 2500, 300, "✈️"),
    prop("Greater Kailash", 3500, 450, GD),
    prop("Panchsheel", 3400, 440, GD),
    special("Blue Tokai", "bonus_roll", "☕"),
]
JAIL = 7

CARDS = [
    ("Diwali bonus from office", 1000),
    ("Sold old Maruti on OLX", 800),
    ("Won a bet at the Kotla", 600),
    ("GST refund arrives", 500),
    ("Traffic challan", -300),
    ("Chhole bhature feast", -200),
    ("Metro card top-up", -150),
    ("Pollution (PUC) fine", -400),
]


def cell(i):
    """Grid (row, col) on an 8x8 perimeter board, clockwise from bottom-left."""
    if i == 0:
        return 8, 1
    if i <= 6:
        return 8 - i, 1
    if i == 7:
        return 1, 1
    if i <= 13:
        return 1, i - 6
    if i == 14:
        return 1, 8
    if i <= 20:
        return i - 13, 8
    if i == 21:
        return 8, 8
    return 8, 29 - i


def tpos(i, k):
    """Token position (left %, top %) on the board for tile i, player slot k."""
    r, c = cell(i)
    dx = (-2.0, 2.0, -2.0, 2.0)[k]
    dy = (-1.4, -1.4, 1.6, 1.6)[k]
    return (c - 0.5) / 8 * 100 + dx, (r - 0.5) / 8 * 100 + dy


# ---------------- shared state (one copy for all phones) ----------------

def fresh(g):
    g.update(players={}, order=[], owner={}, turn=0, round=1, phase="lobby",
             dice=None, log=[], winner=None, anim=None, room_key=secrets.token_urlsafe(4)[:6].upper(), turn_started=time.time(), pending_naka=None, auction_tile=None, auction_owner_pid=None, auction_exchange_offers=[], auction_declined=[], extra_roll_pid=None)


@st.cache_resource
def get_game_v3():
    g = {"lock": threading.RLock()}
    fresh(g)
    return g


G = get_game_v3()
LOCK = G["lock"]


# ---------------- game logic (call with LOCK held) ----------------

def nm(x):
    return "Bank" if x == BANK else G["players"][x]["name"]


def networth(pid):
    owned = sum(BOARD[i]["price"] for i, o in G["owner"].items() if o == pid)
    return G["players"][pid]["cash"] + owned


def log(msg):
    G["log"].insert(0, msg)
    del G["log"][40:]


def move_money(src, dst, amt, note, forced=False):
    if amt <= 0:
        return "Enter an amount above zero."
    if src != BANK:
        have = G["players"][src]["cash"]
        if have < amt:
            if not forced:
                return f"{nm(src)} doesn't have enough cash."
            amt = have
            if amt <= 0:
                log(f"{nm(src)} is broke and can't pay ({note})")
                return None
    if src != BANK:
        G["players"][src]["cash"] -= amt
    if dst != BANK:
        G["players"][dst]["cash"] += amt
    log(f"{nm(src)} → {nm(dst)}: ₹{amt:,} ({note})")
    return None


def join(name, sid):
    if G["phase"] != "lobby" or len(G["players"]) >= MAX_PLAYERS:
        return None
    pid = uuid.uuid4().hex[:8]
    used = {q.get("vslot") for q in G["players"].values()}
    vslot = next(k for k in range(MAX_PLAYERS) if k not in used)   # stable voice seat, survives other players leaving
    G["players"][pid] = {"vslot": vslot, "name": name, "cash": START_CASH, "pos": 0, "sid": sid, "buildings": {}, "baap_card": False, "baap_card_used": False, "skip_turns": 0}
    G["order"].append(pid)
    log(f"{name} joined")
    return pid


def start_game():
    if G["phase"] != "lobby":
        return "Game already started."
    if len(G["order"]) < 2:
        return "Need at least 2 players."
    G["phase"], G["turn"], G["round"] = "roll", 0, 1
    G["turn_started"] = time.time()
    log(f"Game on! {nm(G['order'][0])} goes first")
    return None


def reset():
    fresh(G)
    return None


def check_winner():
    best = max(G["order"], key=networth)
    if networth(best) >= WIN_NETWORTH or G["round"] > MAX_ROUNDS:
        G["phase"], G["winner"] = "over", best
        log(f"🏆 {nm(best)} wins with ₹{networth(best):,}")


LIMITS = {"roll": 30, "auction_exchange": 180}   # seconds; everything else 60
AUCTION_PHASES = ("auction", "auction_exchange")


def limit_for(phase):
    return LIMITS.get(phase, 60)


def clear_auction():
    G["auction_tile"] = None
    G["auction_owner_pid"] = None
    G["auction_exchange_offers"] = []
    G["auction_declined"] = []


def end_turn():
    G["turn"] += 1
    if G["turn"] >= len(G["order"]):
        G["turn"] = 0
        G["round"] += 1
    G["phase"] = "roll"
    G["turn_started"] = time.time()
    G["pending_naka"] = None
    G["extra_roll_pid"] = None
    clear_auction()
    check_winner()


def resolve(pid):
    p = G["players"][pid]
    i = p["pos"]
    t = BOARD[i]
    k = t["kind"]
    if k in OWNABLE:
        owner = G["owner"].get(i)
        if owner is None:
            if p["cash"] >= t["price"]:
                G["phase"] = "buy"
                return
            log(f"{nm(pid)} can't afford {t['name']}")
        elif owner != pid:
            level = G["players"].get(owner, {}).get("buildings", {}).get(i, 0)
            rent = t["rent"] * (level + 1)
            move_money(pid, owner, rent, f"rent at {t['name']} (building level {level})", forced=True)
    elif k == "naka":
        G["phase"] = "naka_choice"
        G["pending_naka"] = pid
        log(f"🚔 {nm(pid)} stopped at Police Naka")
        return
    elif k == "auction":
        G["phase"] = "auction"
        G["auction_tile"] = None
        G["auction_owner_pid"] = pid
        G["auction_exchange_offers"] = []
        G["auction_declined"] = []
        log(f"🔨 {nm(pid)} triggered an auction")
        return
    elif k == "utility":
        log(f"🔌 {nm(pid)} landed on {t['name']}")
    elif k == "bonus_roll":
        G["phase"] = "roll"
        G["extra_roll_pid"] = pid
        G["turn_started"] = time.time()
        log(f"🍃 {nm(pid)} landed on Blue Tokai and earned an extra roll!")
        return
    end_turn()


def do_roll(pid):
    if G["phase"] != "roll" or G["order"][G["turn"]] != pid:
        return "It's not your turn to roll."
    d1 = random.randint(1, 6)
    G["dice"] = (d1,)
    player_state = G["players"][pid]
    if not player_state.get("baap_card_used", False) and not player_state.get("baap_card", False) and random.random() < (1 / 6):
        player_state["baap_card"] = True
        log(f"🎴 {nm(pid)} randomly received ‘Tu Janta hai mera baap kon hai’ (one use, ever)")
    p = G["players"][pid]
    old = p["pos"]
    path = [(old + k) % len(BOARD) for k in range(d1 + 1)]   # every tile hopped over
    p["pos"] = path[-1]
    log(f"🎲 {nm(pid)} rolled {d1} → {BOARD[p['pos']]['name']}")
    if p["pos"] < old:
        move_money(BANK, pid, SALARY, "passed Start")
    resolve(pid)
    if p["pos"] != path[-1]:          # sent to Tihar by the naka
        path.append(p["pos"])
    G["anim"] = {"pid": pid, "path": path, "t0": time.time()}
    if G["phase"] != "roll":
        G["turn_started"] = time.time()
    return None



def use_naka_option(pid):
    if G["phase"] != "naka_choice" or G.get("pending_naka") != pid:
        return "No Police Naka choice is pending."
    player_state = G["players"][pid]
    if not player_state.get("baap_card", False) or player_state.get("baap_card_used", False):
        return "You don't have an unused ‘Tu Janta hai mera baap kon hai’ card."
    player_state["baap_card"] = False
    player_state["baap_card_used"] = True
    log(f"😎 {nm(pid)} used ‘Tu Janta hai mera baap kon hai’ and talked their way out!")
    G["pending_naka"] = None
    end_turn()
    return None


def go_to_tihar(pid):
    """Declining both the fine and special card costs the player their next 3 turns."""
    if G["phase"] != "naka_choice" or G.get("pending_naka") != pid:
        return "No Police Naka choice is pending."
    G["players"][pid]["skip_turns"] = max(0, G["players"][pid].get("skip_turns", 0)) + 3
    log(f"🚔 {nm(pid)} declined the fine and special card: next 3 turns will be skipped")
    G["pending_naka"] = None
    end_turn()
    return None


def select_auction_property(pid, idx):
    if G.get("phase") != "auction" or G.get("auction_owner_pid") != pid:
        return "You are not setting up this auction."
    if G["owner"].get(idx) != pid or BOARD[idx]["kind"] not in ("prop", "metro", "airport", "utility"):
        return "Choose one of your own properties."
    G["auction_tile"] = idx
    G["auction_exchange_offers"] = []
    G["auction_declined"] = []
    G["phase"] = "auction_exchange"
    G["turn_started"] = time.time()
    log(f"🔨 {nm(pid)} offered {BOARD[idx]['name']} for a property exchange")
    return None


def offer_exchange_property(pid, idx):
    if G.get("phase") != "auction_exchange":
        return "No property exchange is active."
    if pid == G.get("auction_owner_pid"):
        return "Wait for another player to offer a property."
    if G["owner"].get(idx) != pid or BOARD[idx]["kind"] not in OWNABLE:
        return "Choose one of your own properties."
    if any(o["pid"] == pid for o in G["auction_exchange_offers"]):
        return "You have already made an offer in this exchange."
    G["auction_declined"] = [x for x in G.get("auction_declined", []) if x != pid]
    G["auction_exchange_offers"].append({"pid": pid, "idx": idx})
    log(f"🤝 {nm(pid)} offered {BOARD[idx]['name']} in exchange")
    return None


def decline_exchange(pid):
    if G.get("phase") != "auction_exchange" or pid == G.get("auction_owner_pid") or pid not in G["players"]:
        return "No property exchange is active."
    if any(o["pid"] == pid for o in G["auction_exchange_offers"]):
        return "You have already made an offer."
    if pid not in G["auction_declined"]:
        G["auction_declined"].append(pid)
        log(f"🙅 {nm(pid)} passed on the exchange")
    return None


def accept_exchange(pid, offer_index):
    if G.get("phase") != "auction_exchange" or G.get("auction_owner_pid") != pid:
        return "Only the auction starter can accept an exchange."
    offers = G.get("auction_exchange_offers", [])
    if offer_index < 0 or offer_index >= len(offers):
        return "That exchange offer is no longer available."
    target_idx = G.get("auction_tile")
    offer = offers[offer_index]
    other_pid, other_idx = offer["pid"], offer["idx"]
    if G["owner"].get(target_idx) != pid or G["owner"].get(other_idx) != other_pid:
        return "Property ownership changed; exchange cancelled."
    G["owner"][target_idx], G["owner"][other_idx] = other_pid, pid
    log(f"🔁 {nm(pid)} exchanged {BOARD[target_idx]['name']} with {nm(other_pid)} for {BOARD[other_idx]['name']}")
    end_turn()
    return None


def finish_exchange(pid):
    if G.get("phase") != "auction_exchange" or G.get("auction_owner_pid") != pid:
        return "Only the auction starter can finish this exchange."
    log("🤝 Property exchange ended without a deal")
    end_turn()
    return None


def build_upgrade(pid, idx, level):
    if G["order"][G["turn"]] != pid or G["phase"] != "roll":
        return "Buildings can be added during your turn."
    if BOARD[idx]["kind"] != "prop" or G["owner"].get(idx) != pid:
        return "You must own this property to upgrade it."
    group = BOARD[idx]["color"]
    group_tiles = [i for i,t in enumerate(BOARD) if t.get("kind") == "prop" and t.get("color") == group]
    if not group_tiles or any(G["owner"].get(i) != pid for i in group_tiles):
        return "Complete the whole colour set before building."
    current = G["players"][pid]["buildings"].get(idx, 0)
    if level != current + 1 or level > 3:
        return "Upgrade in order: 1st upgrade, 2nd upgrade, then hotel."
    cost = 500 * level
    err = move_money(pid, BANK, cost, f"building level {level} at {BOARD[idx]['name']}")
    if err: return err
    G["players"][pid]["buildings"][idx] = level
    log(f"🏠 {nm(pid)} built {'hotel' if level == 3 else f'upgrade {level}'} at {BOARD[idx]['name']}")
    return None


def leave_game(pid):
    if pid not in G["players"]:
        return None
    name = G["players"][pid]["name"]
    in_auction = G["phase"] in AUCTION_PHASES
    was_owner = G.get("auction_owner_pid") == pid
    G["order"] = [x for x in G["order"] if x != pid]
    G["players"].pop(pid, None)
    G["auction_exchange_offers"] = [o for o in G.get("auction_exchange_offers", []) if o["pid"] != pid]
    G["auction_declined"] = [x for x in G.get("auction_declined", []) if x != pid]
    if in_auction and was_owner:
        clear_auction()
        G["phase"] = "roll"
    G["owner"] = {i:o for i,o in G["owner"].items() if o != pid}
    if G["order"]:
        G["turn"] = G["turn"] % len(G["order"])
    else:
        G["phase"] = "lobby"
        G["turn"] = 0
    log(f"{name} left the game")
    G["turn_started"] = time.time()
    return None


def auto_progress():
    if G["phase"] in ("roll", "buy", "naka_choice", "auction", "auction_exchange") and G["order"]:
        elapsed = time.time() - G.get("turn_started", time.time())
        pid = G["order"][G["turn"]]
        if G["phase"] == "roll" and G["players"][pid].get("skip_turns", 0) > 0:
            G["players"][pid]["skip_turns"] -= 1
            log(f"⏭️ {nm(pid)} skips this turn ({G['players'][pid]['skip_turns']} skip(s) remaining)")
            end_turn()
        elif G["phase"] == "roll" and elapsed >= 30:
            log(f"⏱️ Auto-roll for {nm(pid)} after 30 seconds")
            do_roll(pid)
            G["turn_started"] = time.time()
        elif G["phase"] in ("buy", "naka_choice", "auction", "auction_exchange") and elapsed >= limit_for(G["phase"]):
            log(f"⏱️ {nm(pid)} timed out; turn passed")
            end_turn()


def do_buy(pid):
    if G["phase"] != "buy" or G["order"][G["turn"]] != pid:
        return "Nothing to buy right now."
    i = G["players"][pid]["pos"]
    t = BOARD[i]
    err = move_money(pid, BANK, t["price"], f"bought {t['name']}")
    if err:
        return err
    G["owner"][i] = pid
    end_turn()
    return None


def do_pass(pid):
    if G["phase"] not in ("buy", "auction_bid") or G["order"][G["turn"]] != pid:
        return "Nothing to pass on."
    if G["phase"] == "auction_bid":
        log(f"{nm(pid)} passed on the auction")
    else:
        log(f"{nm(pid)} passed on {BOARD[G['players'][pid]['pos']]['name']}")
    end_turn()
    return None


def buy_from_player(buyer, seller, idx, price):
    if G["owner"].get(idx) != seller:
        return f"{nm(seller)} doesn't own that."
    err = move_money(buyer, seller, price, f"{BOARD[idx]['name']} bought from player")
    if err:
        return err
    G["owner"][idx] = buyer
    return None


def run_action(fn, *args):
    with LOCK:
        err = fn(*args)
    if err:
        st.toast(f"⚠️ {err}")
        return
    st.rerun()


# ---------------- rendering ----------------

CSS = """<style>
header[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],footer{display:none!important}
.block-container{padding:.4rem .4rem .5rem!important;max-width:520px}
[data-testid="stVerticalBlock"]{gap:.4rem!important}
[data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important;gap:.35rem!important}
[data-testid="stColumn"],[data-testid="column"]{min-width:0!important}
.stButton button,[data-testid="stPopover"] button{padding:.2rem .3rem!important;min-height:2.8rem!important}

.dbr{background:#48484d;border-radius:12px;padding:6px 5px;color:#fff}
.dbr .hdr{text-align:center;font-weight:600;font-size:13px;margin:0 0 5px}
.dbr .hdr b{color:#8bd45b}
.prow{display:flex;gap:4px;margin-bottom:6px}
.pc{flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:center;padding:3px 1px;border-radius:10px;border:2px solid transparent;line-height:1.15}
.pc.empty{min-height:76px}
.pc.turn{border-color:#3b82f6;background:rgba(59,130,246,.18)}
.pc .r{display:flex;align-items:center;gap:3px}
.pc .av{width:32px;height:32px;border-radius:50%;box-sizing:border-box;border:2px solid #888;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px}
.pc .n{display:flex;gap:2px;max-width:100%;font-size:12px;margin-top:2px}
.pc .nn{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.pc .y{flex:none;color:#b5b5bd}
.pc .a{font-size:13px;font-weight:700}
.pc .nw{font-size:11px;color:#8bd45b}

.tok{border-radius:50%;border:2px solid #fff;box-sizing:border-box;box-shadow:0 2px 3px rgba(0,0,0,.45)}
.tok.mid{width:12px;height:12px}
.tok.on{box-shadow:0 0 0 2px #fff,0 0 8px 3px rgba(255,255,255,.85)}

.bw{display:flex;justify-content:center}
.board{position:relative;container-type:inline-size;display:grid;grid-template-columns:repeat(8,1fr);grid-template-rows:repeat(8,1fr);background:#f2f4f6;border-radius:6px;aspect-ratio:1/1;width:min(100%,calc(100vh - 230px));width:min(100%,calc(100dvh - 230px))}
.t{position:relative;margin:1px;border-radius:3px;overflow:hidden;display:flex;flex-direction:column;align-items:center;justify-content:space-between;padding:1px;min-width:0;font-size:6.5px;font-size:1.75cqw;font-weight:700;text-transform:uppercase;line-height:1.05;text-align:center;color:#fff}
.t.sp{background:#f4f7fa;color:#667}
.t .pr{background:#fff;color:#333;border-radius:3px;padding:0 3px;font-size:7.5px;font-size:2.05cqw}
.t .ic{font-size:13px;font-size:3.5cqw;line-height:1}\n.t .building{font-size:10px;font-size:2.8cqw;line-height:1;background:rgba(255,255,255,.9);border-radius:4px;padding:1px 2px;color:#222}

.mv{position:absolute;width:4.4%;aspect-ratio:1;margin:-2.2% 0 0 -2.2%;z-index:3;pointer-events:none}
.mv .tok{width:100%;height:100%}

.ctr{grid-row:2/8;grid-column:2/8;margin:1px;background:#c5d3dc;border-radius:8px;color:#333;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:3%;gap:1.5%;min-height:0;overflow:hidden}
.ctr .dice{font-size:42px;font-size:12cqw;line-height:1;display:block;transform-origin:center}
.ctr .dice.rolling{animation:shake .6s ease-in-out infinite}
.ctr .clock{font-size:10px;font-size:2.8cqw;font-weight:800;color:#253746;background:rgba(255,255,255,.68);border:1px solid rgba(37,55,70,.25);border-radius:999px;padding:2px 9px;min-width:44px}
.ctr .leaf{font-size:24px;font-size:7cqw;line-height:1;animation:leafpop .8s ease-in-out infinite alternate}
@keyframes leafpop{from{transform:rotate(-12deg) scale(.85)}to{transform:rotate(12deg) scale(1.18)}}
.ctr .tt{font-size:13px;font-size:3.6cqw;font-weight:700}
.ctr .ss{font-size:10px;font-size:2.8cqw}
.ctr .lg{font-size:9px;font-size:2.5cqw;color:#4a5560;width:100%;margin-top:2%}
.ctr .lg div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@keyframes shake{0%{transform:rotate(0)}20%{transform:rotate(-18deg) scale(1.15)}40%{transform:rotate(14deg) scale(1.15)}60%{transform:rotate(-10deg)}80%{transform:rotate(6deg)}100%{transform:rotate(0)}}
</style>"""


def esc(s):
    return html.escape(str(s))


def pcard(pid, S):
    p = S["players"][pid]
    col = PCOL[S["order"].index(pid)]
    turn = " turn" if pid == S["turn"] else ""
    you = '<span class="y">(you)</span>' if pid == S["me"] else ""
    return (f'<div class="pc{turn}"><div class="r">'
            f'<div class="tok mid" style="background:{col}"></div>'
            f'<div class="av" style="background:{col}66;border-color:{col}">{esc(p["name"][:1].upper())}</div></div>'
            f'<div class="n"><span class="nn">{esc(p["name"])}</span>{you}</div>'
            f'<div class="a">₹{p["cash"]:,}</div><div class="nw">(₹{S["nw"][pid]:,})</div></div>')


def tile(i, S):
    t = BOARD[i]
    r, c = cell(i)
    place = f"grid-row:{r};grid-column:{c};"
    name = esc(t["name"])
    if t["kind"] in OWNABLE:
        owner = S["owner"].get(i)
        price = f'₹{t["price"] / 1000:g}k'
        if owner is not None:
            oc = PCOL[S["order"].index(owner)]
            badge = f'<div class="pr" style="background:{oc};color:#fff">{price}</div>'
            ring = f"box-shadow:inset 0 0 0 2px {oc};"
        else:
            badge = f'<div class="pr">{price}</div>'
            ring = ""
        if t["kind"] == "prop":
            style = f"background:{t['color']};{'' if owner is not None else 'opacity:.8;'}{ring}"
            level = S["players"].get(owner, {}).get("buildings", {}).get(i, 0) if owner is not None else 0
            building = ""
            if level:
                marker = "🏨" if level == 3 else ("🏠🏠" if level == 2 else "🏠")
                building = f'<div class="building" title="Building level {level}">{marker}</div>'
            return f'<div class="t" style="{place}{style}">{badge}{building}<div>{name}</div></div>'
        return (f'<div class="t sp" style="{place}{ring}">{badge}'
                f'<div class="ic">{t["icon"]}</div><div>{name}</div></div>')
    return (f'<div class="t sp" style="{place}"><div class="ic">{t["icon"]}</div>'
            f'<div>{name}</div></div>')


def tokens_html(S):
    """Tokens float above the board. A recent move is drawn as a hop-by-hop
    animation that resumes at the right moment on every phone."""
    out, css = [], ""
    A = S["anim"]
    for k, pid in enumerate(S["order"]):
        col = PCOL[k]
        on = " on" if pid == S["turn"] else ""
        if A and A["pid"] == pid:
            pts = [tpos(i, k) for i in A["path"]]
            n = len(pts) - 1
            frames = "".join(
                f"{j / n * 100:.2f}%{{left:{x:.2f}%;top:{y:.2f}%;"
                f"animation-timing-function:cubic-bezier(.4,0,.2,1)}}"
                for j, (x, y) in enumerate(pts)
            )
            css = (f"@keyframes mvp{{{frames}}}"
                   "@keyframes hop{0%,100%{transform:translateY(0) scale(1)}"
                   "50%{transform:translateY(-55%) scale(1.35)}}")
            x, y = pts[-1]
            e = A["elapsed"]
            out.append(
                f'<div class="mv" style="left:{x:.2f}%;top:{y:.2f}%;z-index:5;'
                f'animation:mvp {n * STEP:.2f}s linear -{e:.3f}s 1 both">'
                f'<div class="tok{on}" style="background:{col};'
                f'animation:hop {STEP}s ease-in-out -{e:.3f}s {n} both"></div></div>'
            )
        else:
            x, y = tpos(S["players"][pid]["pos"], k)
            out.append(f'<div class="mv" style="left:{x:.2f}%;top:{y:.2f}%">'
                       f'<div class="tok{on}" style="background:{col}"></div></div>')
    style = f"<style>{css}</style>" if css else ""
    return style + "".join(out)


def center(S):
    d = S["dice"]
    age = S["roll_age"]
    if d:
        shake = f' style="animation:shake .6s ease-out -{age:.2f}s 1"' if age < 0.6 else ""
        dice = f'<div class="dice"{shake}>{DIE[d[0]]}</div>'
    else:
        dice = '<div class="dice">🎲</div>'
    order, players = S["order"], S["players"]
    extra = ""
    if S["phase"] == "over":
        title, sub = f"🏆 {esc(players[S['winner']]['name'])} wins!", f"Net worth ₹{S['nw'][S['winner']]:,}"
    elif S["phase"] == "lobby":
        title, sub = "Waiting for players…", f"{len(order)}/{MAX_PLAYERS} joined"
        extra = '<div class="ss">Others: open the plain app link and join</div>'
    elif S["phase"] == "buy":
        who = players[S["turn"]]
        t = BOARD[who["pos"]]
        title = f"{esc(who['name'])} landed on {esc(t['name'])}"
        sub = f"Price ₹{t['price']:,} · Rent ₹{t['rent']:,}"
    elif S["phase"] == "naka_choice":
        title, sub = "Police Naka!", "Play your line or pay the fine"
    elif S["phase"] in ("auction", "auction_exchange", "auction_bid"):
        title, sub = "🔨 Auction", "Auction corner — resolve the auction"
    else:
        title, sub = f"{esc(players[S['turn']]['name'])}'s turn", "Roll the dice"
    lg = "".join(f"<div>{esc(x)}</div>" for x in S["log"])
    clock = f'<div class="clock">⏱ {S["turn_seconds_left"]}s</div>' if S["phase"] in ("roll", "buy", "naka_choice", "auction", "auction_exchange") else ""
    leaf = '<div class="leaf">🍃</div>' if S.get("extra_roll_pid") == S.get("turn") else ""
    return (f'<div class="ctr">{clock}{dice}{leaf}<div class="tt">{title}</div>'
            f'<div class="ss">{sub}</div>{extra}<div class="lg">{lg}</div></div>')


def board_html(S):
    cards = [pcard(p, S) for p in S["order"]]
    while len(cards) < MAX_PLAYERS:
        cards.append('<div class="pc empty"></div>')
    left = max(0, MAX_ROUNDS - S["round"] + 1)
    hdr = f'<div class="hdr"><b>₹{WIN_NETWORTH:,}</b> net worth to win. Rounds left: {left}</div>'
    tiles = "".join(tile(i, S) for i in range(len(BOARD)))
    return (f'<div class="dbr">{hdr}<div class="prow">{"".join(cards)}</div>'
            f'<div class="bw"><div class="board">{tiles}{center(S)}{tokens_html(S)}</div></div></div>')


def snapshot(me):
    order = list(G["order"])
    live = G["phase"] in ("roll", "buy", "naka_choice", "auction", "auction_exchange", "auction_bid")
    A = G.get("anim")
    age = max(0.0, time.time() - A["t0"]) if A else 999.0
    anim = None
    if A and A["pid"] in G["players"] and age < (len(A["path"]) - 1) * STEP:
        anim = {"pid": A["pid"], "path": list(A["path"]), "elapsed": age}
    return {
        "me": me,
        "order": order,
        "players": {p: dict(G["players"][p]) for p in order},
        "owner": dict(G["owner"]),
        "nw": {p: networth(p) for p in order},
        "turn": order[G["turn"]] if order and live else None,
        "phase": G["phase"],
        "dice": G["dice"],
        "log": list(G["log"][:4]),
        "round": G["round"],
        "winner": G["winner"],
        "anim": anim,
        "roll_age": age,
        "turn_seconds_left": max(0, limit_for(G["phase"]) - int(time.time() - G.get("turn_started", time.time()))),
        "extra_roll_pid": G.get("extra_roll_pid"),
        "auction_owner_pid": G.get("auction_owner_pid"),
        "auction_exchange_offers": list(G.get("auction_exchange_offers", [])),
        "auction_declined": list(G.get("auction_declined", [])),
        "auction_tile": G.get("auction_tile"),
        "room_key": G.get("room_key", ""),
    }


def ctl_key(S):
    """When this changes, the buttons under the board must be redrawn."""
    return (S["phase"], S["turn"], S["winner"], len(S["order"]), len(S.get("auction_exchange_offers", [])), len(S.get("auction_declined", [])), S.get("auction_tile"))


# ---------------- voice call (browser-to-browser, 4-way mesh) ----------------
# Audio goes directly between phones (WebRTC). PeerJS's free public broker is only used
# to introduce the phones to each other. For players on strict mobile networks add a TURN
# server here, e.g. {"urls": "turn:your.server:3478", "username": "u", "credential": "p"}
TURN_SERVERS = []


def voice_widget(room_key, vslot, my_name):
    cfg = json.dumps({
        "room": f"dilli-{room_key}",
        "slot": vslot,
        "max": MAX_PLAYERS,
        "name": my_name,
        "ice": [{"urls": "stun:stun.l.google.com:19302"}, {"urls": "stun:stun1.l.google.com:19302"}] + TURN_SERVERS,
    })
    page = """
<style>
 body{margin:0;font-family:system-ui,sans-serif;font-size:13px;color:#222}
 .row{display:flex;gap:6px;align-items:center}
 button{flex:1;padding:8px;border:0;border-radius:8px;font-size:14px;font-weight:600;background:#2f7de1;color:#fff}
 button.off{background:#888} button.mute{background:#e5473d;flex:0 0 auto;padding:8px 14px}
 #who{margin-top:6px;display:flex;flex-wrap:wrap;gap:6px}
 .chip{padding:2px 8px;border-radius:12px;background:#e8eef7}
 .chip.talk{background:#4cb050;color:#fff}
 #st{margin-top:4px;color:#666}
</style>
<div class="row"><button id="go">🎙️ Join voice call</button><button id="mute" class="mute" style="display:none">Mute</button></div>
<div id="who"></div><div id="st"></div><div id="audio"></div>
<script src="https://unpkg.com/peerjs@1.5.4/dist/peerjs.min.js"></script>
<script>
const C = __CFG__;
const $ = id => document.getElementById(id);
const idOf = k => C.room + "-s" + k;
let peer, stream, muted = false, joined = false;
const calls = {};   // slot -> {call, name}
const names = {};

function render(){
  const w = $("who"); w.innerHTML = "";
  const me = document.createElement("span"); me.className = "chip"; me.id = "chip-me";
  me.textContent = (muted ? "🔇 " : "🎙️ ") + C.name + " (you)"; w.appendChild(me);
  Object.keys(calls).forEach(k => {
    if (!calls[k].live) return;
    const c = document.createElement("span"); c.className = "chip"; c.id = "chip-" + k;
    c.textContent = "🔊 " + (names[k] || "Player"); w.appendChild(c);
  });
}

function attach(k, call){
  calls[k] = {call, live: false};
  call.on("stream", rs => {
    let a = document.getElementById("a" + k);
    if (!a) { a = document.createElement("audio"); a.id = "a" + k; a.autoplay = true; a.playsInline = true; $("audio").appendChild(a); }
    a.srcObject = rs; a.play().catch(()=>{});
    calls[k].live = true; render(); watchLevel(k, rs);
  });
  const drop = () => { if (calls[k] && calls[k].call === call) { delete calls[k]; const a = document.getElementById("a"+k); if (a) a.remove(); render(); } };
  call.on("close", drop); call.on("error", drop);
}

function watchLevel(k, rs){
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const an = ctx.createAnalyser(); an.fftSize = 256;
    ctx.createMediaStreamSource(rs).connect(an);
    const buf = new Uint8Array(an.frequencyBinCount);
    setInterval(() => {
      an.getByteFrequencyData(buf);
      const lvl = buf.reduce((a,b)=>a+b,0) / buf.length;
      const chip = document.getElementById("chip-" + k);
      if (chip) chip.classList.toggle("talk", lvl > 12);
    }, 250);
  } catch(e) {}
}

function dial(){
  // higher seat calls lower seat; lower seat just answers. Retry until connected.
  for (let k = 0; k < C.slot; k++) {
    if (calls[k]) continue;
    const call = peer.call(idOf(k), stream, {metadata: {name: C.name}});
    if (!call) continue;
    attach(k, call);
    const conn = peer.connect(idOf(k), {metadata: {name: C.name}});
    conn.on("data", d => { if (d && d.name) { names[k] = d.name; render(); } });
    setTimeout(() => { if (calls[k] && !calls[k].live) { try { calls[k].call.close(); } catch(e){} delete calls[k]; } }, 6000);
  }
}

async function join(){
  $("st").textContent = "Asking for microphone…";
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
  } catch(e) { $("st").textContent = "Microphone blocked — allow it in your browser settings and try again."; return; }
  peer = new Peer(idOf(C.slot), {config: {iceServers: C.ice}});
  peer.on("open", () => {
    joined = true; $("go").textContent = "Leave voice call"; $("go").className = "off"; $("mute").style.display = "";
    $("st").textContent = "Connected. Others join automatically when they tap Join."; render();
    dial(); window._dial = setInterval(dial, 3000);
  });
  peer.on("call", call => {
    const k = parseInt(call.peer.split("-s").pop(), 10);
    call.answer(stream); attach(k, call);
    names[k] = (call.metadata && call.metadata.name) || names[k];
  });
  peer.on("connection", conn => {
    const k = parseInt(conn.peer.split("-s").pop(), 10);
    conn.on("open", () => conn.send({name: C.name}));
    if (conn.metadata && conn.metadata.name) { names[k] = conn.metadata.name; render(); }
  });
  peer.on("error", e => {
    if (e.type === "unavailable-id") $("st").textContent = "This seat is already on voice from another tab. Close it and retry.";
    else if (e.type !== "peer-unavailable") $("st").textContent = "Voice error: " + e.type;
  });
}

function leave(){
  clearInterval(window._dial);
  Object.values(calls).forEach(c => { try { c.call.close(); } catch(e){} });
  Object.keys(calls).forEach(k => delete calls[k]);
  if (peer) peer.destroy();
  if (stream) stream.getTracks().forEach(t => t.stop());
  joined = false; muted = false; $("audio").innerHTML = "";
  $("go").textContent = "🎙️ Join voice call"; $("go").className = ""; $("mute").style.display = "none";
  $("who").innerHTML = ""; $("st").textContent = "";
}

$("go").onclick = () => joined ? leave() : join();
$("mute").onclick = () => {
  muted = !muted; stream.getAudioTracks().forEach(t => t.enabled = !muted);
  $("mute").textContent = muted ? "Unmute" : "Mute"; render();
};
</script>
""".replace("__CFG__", cfg)
    components.html(page, height=110)


# ---------------- app ----------------

st.set_page_config(page_title="Dilli Bank Roll", page_icon="🏛️")
st.markdown(CSS, unsafe_allow_html=True)

# Each browser session gets its own ID; a seat belongs to the session that joined it.
if "sid" not in st.session_state:
    st.session_state.sid = uuid.uuid4().hex
SID = st.session_state.sid

me = st.query_params.get("p")
with LOCK:
    pl = G["players"].get(me)
    mine = bool(pl) and pl.get("sid") == SID
    phase_now = G["phase"]
    full = len(G["players"]) >= MAX_PLAYERS
    seats = {pid: q["name"] for pid, q in G["players"].items()}

if not mine:
    st.title("🏛️ Dilli Bank Roll")
    if phase_now == "lobby" and not full:
        st.info(f"Room key: **{G.get('room_key', '')}** — share it with friends")
        with st.form("join"):
            name = st.text_input("Your name")
            room_key = st.text_input("Room key", value=G.get("room_key", ""))
            go = st.form_submit_button("Join game", use_container_width=True)
        if go and name.strip():
            if room_key.strip().upper() != G.get("room_key", ""):
                st.error("Incorrect room key.")
                st.stop()
            with LOCK:
                new_pid = join(name.strip()[:14], SID)
            if new_pid is None:
                st.error("Couldn't join (game full or already started).")
            else:
                st.query_params["p"] = new_pid
                st.rerun()
    elif phase_now == "lobby":
        st.warning("All 4 seats are taken.")
    else:
        st.warning("A game is already in progress.")

    if seats:
        st.markdown("**Already in the game? Tap your name**")
        for pid, n in seats.items():
            if st.button(n, key=f"re_{pid}", use_container_width=True):
                with LOCK:
                    if pid in G["players"]:
                        G["players"][pid]["sid"] = SID
                st.query_params["p"] = pid
                st.rerun()
    st.stop()


@st.fragment(run_every=1)
def board_view(me):
    """Redraws the board every second. If the turn/phase changed, redraw the buttons too."""
    with LOCK:
        auto_progress()
        S = snapshot(me) if me in G["players"] else None
    if S is None or st.session_state.get("ctl_key") != ctl_key(S):
        st.rerun()
        return
    st.markdown(board_html(S), unsafe_allow_html=True)


def controls(S, me):
    players, order, phase = S["players"], S["order"], S["phase"]
    my_turn = S["turn"] == me
    turn_name = players[S["turn"]]["name"] if S["turn"] else ""

    if phase == "buy" and my_turn:
        c = st.columns([2, 2, 1, 1])
        t = BOARD[players[me]["pos"]]
        if c[0].button(f"✅ Buy ₹{t['price']:,}", type="primary", use_container_width=True, key="buy"):
            run_action(do_buy, me)
        if c[1].button("Pass", use_container_width=True, key="pass"):
            run_action(do_pass, me)
        p1, p2 = c[2], c[3]
    else:
        c = st.columns([4, 1, 1])
        p1, p2 = c[1], c[2]
        if phase == "lobby":
            if c[0].button("▶️ Start game", type="primary", use_container_width=True,
                           disabled=len(order) < 2, key="start"):
                run_action(start_game)
        elif phase == "roll" and my_turn:
            if c[0].button("🎲 Roll dice", type="primary", use_container_width=True, key="roll"):
                run_action(do_roll, me)
        elif phase == "roll":
            c[0].button(f"⏳ {turn_name}'s turn", disabled=True, use_container_width=True, key="wait")
        elif phase == "buy":
            c[0].button(f"{turn_name} is deciding…", disabled=True, use_container_width=True, key="wait")
        elif phase == "naka_choice" and my_turn:
            c[0].markdown("**Police Naka: choose your move below**")
        elif phase == "auction" and my_turn:
            c[0].markdown("**Auction corner: choose below**")
        elif phase in ("naka_choice", "auction", "auction_exchange", "auction_bid"):
            c[0].button(f"{turn_name} is deciding…", disabled=True, use_container_width=True, key="wait")
        else:
            c[0].button("🏆 Game over", disabled=True, use_container_width=True, key="wait")

    if phase == "naka_choice" and my_turn:
        a, b = st.columns(2)
        if S["players"][me].get("baap_card", False) and not S["players"][me].get("baap_card_used", False):
            if a.button("😎 Tu Janta hai mera baap kon hai", use_container_width=True, key="naka_free"):
                run_action(use_naka_option, me)
        else:
            a.caption("No special card available — skip 3 turns or choose the other option")
        if b.button("⏭️ Decline fine/card — skip 3 turns", use_container_width=True, key="naka_fine"):
            run_action(go_to_tihar, me)
    if phase == "auction" and my_turn:
        st.markdown("**Choose one of your properties to put up for exchange:**")
        owned = [i for i, owner in S["owner"].items() if owner == me and BOARD[i]["kind"] in OWNABLE]
        if not owned:
            st.info("You don't own any properties to offer. End this auction to continue.")
        cols = st.columns(2)
        for n, idx in enumerate(owned):
            if cols[n % 2].button(f"🏠 {BOARD[idx]['name']}", key=f"auction_prop_{idx}", use_container_width=True):
                run_action(select_auction_property, me, idx)
        if st.button("Skip exchange", key="auction_skip"):
            with LOCK:
                end_turn()
            st.rerun()
    if phase == "auction_exchange":
        starter = S.get("auction_owner_pid")
        offered = S.get("auction_tile")
        if offered is None or not isinstance(offered, int) or not (0 <= offered < len(BOARD)) or starter not in players:
            st.warning("This exchange is missing its offered property. The auction can be safely cancelled.")
            if starter == me and st.button("Cancel exchange", key="cancel_invalid_exchange"):
                with LOCK:
                    end_turn()
                st.rerun()
        elif starter == me:
            st.markdown(f"**You offered {BOARD[offered]['name']}. Accept an exchange below:**")
            offers = S.get("auction_exchange_offers", [])
            if offers:
                cols = st.columns(2)
                for n, offer in enumerate(offers):
                    label = f"Accept {BOARD[offer['idx']]['name']} ({players[offer['pid']]['name']})"
                    if cols[n % 2].button(label, key=f"accept_exchange_{n}", use_container_width=True):
                        run_action(accept_exchange, me, n)
            waiting = [players[x]["name"] for x in S["order"]
                       if x != me and x not in S.get("auction_declined", [])
                       and not any(o["pid"] == x for o in offers)]
            if waiting:
                st.caption("⏳ Waiting for: " + ", ".join(waiting) + ". The auction stays open until everyone answers or you finish it.")
            elif not offers:
                st.caption("Everyone passed. Finish when you're ready.")
            if st.button("Finish without exchange", key="finish_exchange"):
                run_action(finish_exchange, me)
        else:
            st.markdown(f"**{players[starter]['name']} offered {BOARD[offered]['name']}. Click one of your properties to offer in exchange:**")
            owned = [i for i, owner in S["owner"].items() if owner == me and BOARD[i]["kind"] in OWNABLE]
            cols = st.columns(2)
            for n, idx in enumerate(owned):
                if cols[n % 2].button(f"🔁 {BOARD[idx]['name']}", key=f"exchange_prop_{idx}", use_container_width=True):
                    run_action(offer_exchange_property, me, idx)
            if not owned:
                st.caption("You don't own any properties to offer.")
            mine_offered = any(o["pid"] == me for o in S.get("auction_exchange_offers", []))
            if mine_offered:
                st.caption("✅ Your offer is in. Waiting for " + players[starter]["name"] + " to decide.")
            elif me in S.get("auction_declined", []):
                st.caption("You passed. Waiting for the auction to finish.")
            elif st.button("🙅 Pass (no offer)", key="exchange_decline", use_container_width=True):
                run_action(decline_exchange, me)
    if phase == "auction_bid" and my_turn:
        if st.button("Finish auction / pass", use_container_width=True, key="auction_pass"):
            run_action(do_pass, me)
    if phase == "roll" and my_turn:
        owned_props = [i for i,o in S["owner"].items() if o == me and BOARD[i]["kind"] == "prop"]
        complete_set_props = []
        for i in owned_props:
            color = BOARD[i]["color"]
            group_tiles = [j for j,t in enumerate(BOARD) if t.get("kind") == "prop" and t.get("color") == color]
            if group_tiles and all(S["owner"].get(j) == me for j in group_tiles):
                complete_set_props.append(i)
        if complete_set_props:
            with st.expander("🏠 Build on a complete colour set"):
                idx = st.selectbox("Property", complete_set_props, format_func=lambda i: BOARD[i]["name"], key="build_prop")
                current_level = S["players"][me].get("buildings", {}).get(idx, 0)
                if current_level < 3:
                    level = current_level + 1
                    level_label = {1:"1st upgrade",2:"2nd upgrade",3:"Hotel"}[level]
                    st.caption(f"Next upgrade: {level_label} · Cost ₹{500 * level:,}")
                    if st.button("Build / upgrade", key="build_btn", use_container_width=True):
                        run_action(build_upgrade, me, idx, level)
                else:
                    st.caption("This property already has a hotel.")
        elif owned_props:
            st.caption("Complete every property in a colour group before building.")


    with p1.popover("💸", use_container_width=True):
        amt = st.number_input("Amount (₹)", min_value=0, step=100, value=0, key="amt")
        if st.button("Collect from bank", use_container_width=True, key="fromBank"):
            run_action(move_money, BANK, me, amt, "bank payout")
        if st.button("Pay bank", use_container_width=True, key="toBank"):
            run_action(move_money, me, BANK, amt, "bank payment")
        others = {p: players[p]["name"] for p in order if p != me}
        if others:
            target = st.selectbox("Player", list(others), format_func=lambda x: others[x], key="target")
            if st.button("Pay player", use_container_width=True, key="payPlayer"):
                run_action(move_money, me, target, amt, "payment")
            theirs = [i for i, o in S["owner"].items() if o == target]
            if theirs:
                pidx = st.selectbox("Their property", theirs,
                                    format_func=lambda i: BOARD[i]["name"], key="tprop")
                if st.button("Buy it for the amount above", use_container_width=True, key="buyFrom"):
                    run_action(buy_from_player, me, target, pidx, amt)

    with p2.popover("⚙️", use_container_width=True):
        st.caption("Reset ends the game for everyone.")
        if st.button("Leave game", use_container_width=True, key="leave"):
            with LOCK:
                leave_game(me)
            st.query_params.clear()
            st.rerun()
        if st.button("Reset game (everyone leaves)", use_container_width=True, key="reset"):
            run_action(reset)


with LOCK:
    S0 = snapshot(me)
st.session_state.ctl_key = ctl_key(S0)
board_view(me)
controls(S0, me)
with LOCK:
    _pl = G["players"].get(me)
    _slot = _pl.get("vslot", G["order"].index(me) if me in G["order"] else 0) if _pl else 0
    _rk, _nm = G.get("room_key", ""), (_pl or {}).get("name", "Player")
with st.expander("🎙️ Voice call", expanded=False):
    voice_widget(_rk, _slot, _nm)
