# app.py
import html
import random
import threading
import uuid

import streamlit as st
from streamlit.errors import StreamlitAPIException

# ---------------- settings ----------------
MAX_PLAYERS = 4
START_CASH = 15_000
SALARY = 2_000          # for passing / landing on START
WIN_NETWORTH = 30_000   # first to this net worth wins
MAX_ROUNDS = 40         # otherwise richest after this many rounds wins
TAX = 1_000
FINE = 500
BANK = "BANK"

PCOL = ["#2f7de1", "#4cb050", "#e5473d", "#f2c230"]   # blue, green, red, yellow
OWNABLE = {"prop", "metro", "airport"}
DIE = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}

BR, GR, TE, OR = "#a0623b", "#7aa84a", "#3c8f6d", "#e8a13a"
PK, BL, PU, RD, GD = "#a8456b", "#4a90d9", "#7a4fb5", "#e4623b", "#d9b13b"


def prop(name, price, rent, color):
    return {"name": name, "kind": "prop", "price": price, "rent": rent, "color": color}


def special(name, kind, icon):
    return {"name": name, "kind": kind, "icon": icon}


# 28 tiles, clockwise from START (bottom-left)
BOARD = [
    special("Start", "start", "⬆️"),                    # 0
    prop("Paharganj", 1000, 100, BR),                  # 1
    prop("Mayur Vihar", 1100, 120, BR),                # 2
    special("Chance", "chance", "❓"),                  # 3
    prop("Rohini", 1200, 140, GR),                     # 4
    prop("Dwarka", 1400, 160, GR),                     # 5
    {"name": "Blue Line", "kind": "metro", "price": 2000, "rent": 250, "icon": "🚇"},   # 6
    prop("Pitampura", 1500, 180, TE),                  # 7
    special("Tihar", "jail", "🏛️"),                    # 8 (just visiting)
    prop("Karol Bagh", 1600, 200, TE),                 # 9
    prop("Chandni Chowk", 1800, 220, OR),              # 10
    prop("Nehru Place", 2000, 250, OR),                # 11
    special("Chance", "chance", "❓"),                  # 12
    prop("Lajpat Nagar", 2200, 280, PK),               # 13
    special("India Gate", "park", "🕌"),               # 14
    prop("Saket", 2400, 300, PK),                      # 15
    prop("Hauz Khas", 2600, 320, PU),                  # 16
    {"name": "IGI Airport", "kind": "airport", "price": 2500, "rent": 300, "icon": "✈️"},  # 17
    prop("Vasant Kunj", 2800, 350, PU),                # 18
    special("Income Tax", "tax", "🧾"),                 # 19
    prop("Connaught Pl", 3000, 400, RD),               # 20
    prop("Defence Col", 3200, 420, RD),                # 21
    special("Police Naka", "naka", "🚔"),               # 22 (go to Tihar, pay fine)
    special("Chance", "chance", "❓"),                  # 23
    prop("Sarojini Nagar", 1300, 150, BL),             # 24
    {"name": "Yellow Line", "kind": "metro", "price": 2000, "rent": 250, "icon": "🚇"},  # 25
    prop("Janakpuri", 1700, 200, BL),                  # 26
    prop("Greater Kailash", 3500, 450, GD),            # 27
]
JAIL = 8

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
    """Grid (row, col) on a 9-row x 7-col perimeter board."""
    if i == 0:
        return 9, 1
    if i <= 7:
        return 9 - i, 1
    if i == 8:
        return 1, 1
    if i <= 13:
        return 1, i - 7
    if i == 14:
        return 1, 7
    if i <= 21:
        return i - 13, 7
    if i == 22:
        return 9, 7
    return 9, 29 - i


# ---------------- shared state (one copy for all phones) ----------------

def fresh(g):
    g.update(players={}, order=[], owner={}, turn=0, round=1, phase="lobby",
             dice=None, log=[], winner=None)


@st.cache_resource
def get_game_v2():
    g = {"lock": threading.RLock()}
    fresh(g)
    return g


G = get_game_v2()
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


def join(name):
    if G["phase"] != "lobby" or len(G["players"]) >= MAX_PLAYERS:
        return None
    pid = uuid.uuid4().hex[:8]
    G["players"][pid] = {"name": name, "cash": START_CASH, "pos": 0}
    G["order"].append(pid)
    log(f"{name} joined")
    return pid


def start_game():
    if G["phase"] != "lobby":
        return "Game already started."
    if len(G["order"]) < 2:
        return "Need at least 2 players."
    G["phase"], G["turn"], G["round"] = "roll", 0, 1
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


def end_turn():
    G["turn"] += 1
    if G["turn"] >= len(G["order"]):
        G["turn"] = 0
        G["round"] += 1
    G["phase"] = "roll"
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
            move_money(pid, owner, t["rent"], f"rent at {t['name']}", forced=True)
    elif k == "chance":
        text, delta = random.choice(CARDS)
        log(f"❓ {nm(pid)}: {text}")
        if delta > 0:
            move_money(BANK, pid, delta, "chance")
        else:
            move_money(pid, BANK, -delta, "chance", forced=True)
    elif k == "tax":
        move_money(pid, BANK, TAX, "income tax", forced=True)
    elif k == "naka":
        p["pos"] = JAIL
        log(f"🚔 {nm(pid)} caught at the naka, sent to Tihar")
        move_money(pid, BANK, FINE, "fine", forced=True)
    end_turn()


def do_roll(pid):
    if G["phase"] != "roll" or G["order"][G["turn"]] != pid:
        return "It's not your turn to roll."
    d1, d2 = random.randint(1, 6), random.randint(1, 6)
    G["dice"] = (d1, d2)
    p = G["players"][pid]
    old = p["pos"]
    p["pos"] = (old + d1 + d2) % len(BOARD)
    log(f"🎲 {nm(pid)} rolled {d1}+{d2} → {BOARD[p['pos']]['name']}")
    if p["pos"] < old:
        move_money(BANK, pid, SALARY, "passed Start")
    resolve(pid)
    return None


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
    if G["phase"] != "buy" or G["order"][G["turn"]] != pid:
        return "Nothing to pass on."
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
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun()


# ---------------- rendering ----------------

CSS = """<style>
.block-container{padding:.6rem .5rem 3rem!important;max-width:520px}
.dbr{background:#48484d;border-radius:14px;padding:8px 6px 10px;color:#fff}
.dbr .hdr{text-align:center;font-weight:600;font-size:15px;margin:2px 0 8px}
.dbr .hdr b{color:#8bd45b}
.prow{display:flex;justify-content:space-between;margin:4px 0 8px}
.pc{display:flex;flex-direction:column;align-items:center;width:50%;min-height:20px}
.pc .r{display:flex;align-items:center;gap:6px}
.pc .av{width:44px;height:44px;border-radius:50%;box-sizing:border-box;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px}
.pc .n{font-size:14px;margin-top:3px}
.pc .a{font-size:15px;font-weight:700}
.pc .a span{color:#8bd45b}
.tok{width:14px;height:14px;border-radius:50%;border:2px solid #fff;box-sizing:border-box;box-shadow:0 2px 3px rgba(0,0,0,.45)}
.tok.big{width:26px;height:26px;border-width:3px}
.tok.on{box-shadow:0 0 0 2px #fff,0 0 9px 3px rgba(255,255,255,.85)}
.board{display:grid;grid-template-columns:repeat(7,1fr);grid-template-rows:repeat(9,1fr);gap:2px;background:#f2f4f6;padding:2px;border-radius:6px;aspect-ratio:7/9.4}
.t{position:relative;border-radius:3px;overflow:hidden;display:flex;flex-direction:column;align-items:center;justify-content:space-between;padding:2px 1px;min-width:0;font-size:7.5px;font-weight:700;text-transform:uppercase;line-height:1.05;text-align:center;color:#fff}
.t.sp{background:#f4f7fa;color:#667}
.t .pr{background:#fff;color:#333;border-radius:3px;padding:0 3px;font-size:8.5px}
.t .ic{font-size:15px;line-height:1}
.tk{position:absolute;inset:0;display:flex;flex-wrap:wrap;align-items:center;justify-content:center;align-content:center;gap:1px;padding:4px}
.ctr{grid-row:2/9;grid-column:2/7;background:#c5d3dc;border-radius:8px;color:#333;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:6px;gap:4px}
.ctr .dice{font-size:34px;line-height:1}
.ctr .tt{font-size:14px;font-weight:700}
.ctr .ss{font-size:11px}
.ctr .lg{font-size:9.5px;color:#4a5560;width:100%;margin-top:4px}
.ctr .lg div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style>"""


def esc(s):
    return html.escape(str(s))


def pcard(pid, S, right):
    p = S["players"][pid]
    col = PCOL[S["order"].index(pid)]
    ring = "#3b82f6" if pid == S["turn"] else "#6b6b72"
    av = (f'<div class="av" style="background:{col}55;border:3px solid {ring}">'
          f'{esc(p["name"][:1].upper())}</div>')
    tok = f'<div class="tok big" style="background:{col}"></div>'
    row = av + tok if right else tok + av
    you = " (you)" if pid == S["me"] else ""
    return (f'<div class="pc"><div class="r">{row}</div>'
            f'<div class="n">{esc(p["name"])}{you}</div>'
            f'<div class="a">₹{p["cash"]:,} <span>(₹{S["nw"][pid]:,})</span></div></div>')


def tile(i, S, at):
    t = BOARD[i]
    r, c = cell(i)
    place = f"grid-row:{r};grid-column:{c};"
    toks = "".join(
        f'<div class="tok{" on" if pid == S["turn"] else ""}" '
        f'style="background:{PCOL[S["order"].index(pid)]}"></div>'
        for pid in at.get(i, [])
    )
    tk = f'<div class="tk">{toks}</div>' if toks else ""
    name = esc(t["name"])
    if t["kind"] in OWNABLE:
        owner = S["owner"].get(i)
        if owner:
            oc = PCOL[S["order"].index(owner)]
            badge = f'<div class="pr" style="background:{oc};color:#fff">₹{t["price"] / 1000:g}k</div>'
            ring = f"box-shadow:inset 0 0 0 2px {oc};"
        else:
            badge = f'<div class="pr">₹{t["price"] / 1000:g}k</div>'
            ring = ""
        if t["kind"] == "prop":
            style = f"background:{t['color']};{'' if owner else 'opacity:.8;'}{ring}"
            return (f'<div class="t" style="{place}{style}">{badge}<div>{name}</div>{tk}</div>')
        return (f'<div class="t sp" style="{place}{ring}">{badge}'
                f'<div class="ic">{t["icon"]}</div><div>{name}</div>{tk}</div>')
    return (f'<div class="t sp" style="{place}"><div class="ic">{t["icon"]}</div>'
            f'<div>{name}</div>{tk}</div>')


def center(S):
    d = S["dice"]
    dice = f'<div class="dice">{DIE[d[0]]} {DIE[d[1]]}</div>' if d else '<div class="dice">🎲</div>'
    order, players = S["order"], S["players"]
    if S["phase"] == "over":
        title, sub = f"🏆 {esc(players[S['winner']]['name'])} wins!", f"Net worth ₹{S['nw'][S['winner']]:,}"
    elif S["phase"] == "lobby":
        title, sub = "Waiting for players…", f"{len(order)}/{MAX_PLAYERS} joined"
    elif S["phase"] == "buy":
        who = players[S["turn"]]
        t = BOARD[who["pos"]]
        title = f"{esc(who['name'])} landed on {esc(t['name'])}"
        sub = f"Price ₹{t['price']:,} · Rent ₹{t['rent']:,}"
    else:
        title, sub = f"{esc(players[S['turn']]['name'])}'s turn", "Roll the dice"
    lg = "".join(f"<div>{esc(x)}</div>" for x in S["log"])
    return (f'<div class="ctr">{dice}<div class="tt">{title}</div>'
            f'<div class="ss">{sub}</div><div class="lg">{lg}</div></div>')


def board_html(S):
    at = {}
    for pid in S["order"]:
        at.setdefault(S["players"][pid]["pos"], []).append(pid)
    order = S["order"]
    cards = [pcard(p, S, right=(n % 2 == 1)) for n, p in enumerate(order)]
    while len(cards) < 4:
        cards.append('<div class="pc"></div>')
    left = max(0, MAX_ROUNDS - S["round"] + 1)
    hdr = f'<div class="hdr"><b>₹{WIN_NETWORTH:,}</b> net worth to win. Rounds left: {left}</div>'
    tiles = "".join(tile(i, S, at) for i in range(len(BOARD)))
    return (f'<div class="dbr">{hdr}<div class="prow">{cards[0]}{cards[1]}</div>'
            f'<div class="board">{tiles}{center(S)}</div>'
            f'<div class="prow" style="margin-top:8px">{cards[2]}{cards[3]}</div></div>')


def snapshot(me):
    order = list(G["order"])
    live = G["phase"] in ("roll", "buy")
    return {
        "me": me,
        "order": order,
        "players": {p: dict(G["players"][p]) for p in order},
        "owner": dict(G["owner"]),
        "nw": {p: networth(p) for p in order},
        "turn": order[G["turn"]] if order and live else None,
        "phase": G["phase"],
        "dice": G["dice"],
        "log": list(G["log"][:3]),
        "round": G["round"],
        "winner": G["winner"],
    }


# ---------------- app ----------------

st.set_page_config(page_title="Dilli Bank Roll", page_icon="🏛️")
st.markdown(CSS, unsafe_allow_html=True)

me = st.query_params.get("p")
with LOCK:
    joined = me in G["players"]
    phase_now = G["phase"]
    full = len(G["players"]) >= MAX_PLAYERS

if not joined:
    st.title("🏛️ Dilli Bank Roll")
    if phase_now != "lobby":
        st.warning("A game is already in progress.")
        st.stop()
    if full:
        st.warning("All 4 seats are taken.")
        st.stop()
    with st.form("join"):
        name = st.text_input("Your name")
        go = st.form_submit_button("Join game", use_container_width=True)
    if go and name.strip():
        with LOCK:
            new_pid = join(name.strip()[:14])
        if new_pid is None:
            st.error("Couldn't join (game full or already started).")
            st.stop()
        st.query_params["p"] = new_pid
        st.rerun()
    st.stop()


@st.fragment(run_every=2)
def game_view(me):
    with LOCK:
        if me not in G["players"]:
            S = None
        else:
            S = snapshot(me)
    if S is None:
        st.rerun()
        return

    st.markdown(board_html(S), unsafe_allow_html=True)

    order, players = S["order"], S["players"]
    my_turn = S["turn"] == me

    if S["phase"] == "over":
        st.success(f"🏆 {players[S['winner']]['name']} wins with ₹{S['nw'][S['winner']]:,}!")
    elif S["phase"] == "lobby":
        st.caption("Share this page's link. Each phone opens it, enters a name and joins.")
        if st.button("▶️ Start game", type="primary", use_container_width=True,
                     disabled=len(order) < 2, key="start"):
            run_action(start_game)
    elif S["phase"] == "roll":
        if my_turn:
            if st.button("🎲 Roll dice", type="primary", use_container_width=True, key="roll"):
                run_action(do_roll, me)
        else:
            st.caption(f"Waiting for {players[S['turn']]['name']} to roll…")
    elif S["phase"] == "buy":
        if my_turn:
            t = BOARD[players[me]["pos"]]
            st.markdown(f"**Buy {t['name']} for ₹{t['price']:,}?** (rent ₹{t['rent']:,})")
            c1, c2 = st.columns(2)
            if c1.button("✅ Buy", type="primary", use_container_width=True, key="buy"):
                run_action(do_buy, me)
            if c2.button("Pass", use_container_width=True, key="pass"):
                run_action(do_pass, me)
        else:
            st.caption(f"{players[S['turn']]['name']} is deciding whether to buy…")

    with st.expander("💸 Bank & trades"):
        amt = st.number_input("Amount (₹)", min_value=0, step=100, value=0, key="amt")
        c1, c2 = st.columns(2)
        if c1.button("Collect from bank", use_container_width=True, key="fromBank"):
            run_action(move_money, BANK, me, amt, "bank payout")
        if c2.button("Pay bank", use_container_width=True, key="toBank"):
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

    with st.expander("⚙️ Game"):
        if st.button("Reset game (everyone leaves)", use_container_width=True, key="reset"):
            run_action(reset)


game_view(me)
