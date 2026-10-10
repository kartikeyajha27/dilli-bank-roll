# app.py
import html
import random
import threading
import time
import uuid

import streamlit as st

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
OWNABLE = {"prop", "metro", "airport"}
DIE = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}

BR, GR, TE, OR = "#a0623b", "#7aa84a", "#3c8f6d", "#e8a13a"
PK, BL, PU, RD, GD = "#a8456b", "#4a90d9", "#7a4fb5", "#e4623b", "#d9b13b"


def prop(name, price, rent, color):
    return {"name": name, "kind": "prop", "price": price, "rent": rent, "color": color}


def special(name, kind, icon):
    return {"name": name, "kind": kind, "icon": icon}


# 28 tiles on an 8x8 square board, clockwise from START (bottom-left)
BOARD = [
    special("Start", "start", "⬆️"),                    # 0  corner
    prop("Paharganj", 1000, 100, BR),                  # 1
    prop("Mayur Vihar", 1100, 120, BR),                # 2
    special("Chance", "chance", "❓"),                  # 3
    prop("Rohini", 1200, 140, GR),                     # 4
    prop("Dwarka", 1400, 160, GR),                     # 5
    {"name": "Blue Line", "kind": "metro", "price": 2000, "rent": 250, "icon": "🚇"},   # 6
    special("Tihar", "jail", "🏛️"),                    # 7  corner (just visiting)
    prop("Pitampura", 1500, 180, TE),                  # 8
    prop("Karol Bagh", 1600, 200, TE),                 # 9
    prop("Chandni Chowk", 1800, 220, OR),              # 10
    prop("Nehru Place", 2000, 250, OR),                # 11
    special("Chance", "chance", "❓"),                  # 12
    prop("Lajpat Nagar", 2200, 280, PK),               # 13
    special("India Gate", "park", "🕌"),               # 14 corner
    prop("Saket", 2400, 300, PK),                      # 15
    prop("Hauz Khas", 2600, 320, PU),                  # 16
    {"name": "IGI Airport", "kind": "airport", "price": 2500, "rent": 300, "icon": "✈️"},  # 17
    prop("Vasant Kunj", 2800, 350, PU),                # 18
    special("Income Tax", "tax", "🧾"),                 # 19
    prop("Connaught Pl", 3000, 400, RD),               # 20
    special("Police Naka", "naka", "🚔"),               # 21 corner (go to Tihar, pay fine)
    prop("Defence Col", 3200, 420, RD),                # 22
    special("Chance", "chance", "❓"),                  # 23
    prop("Sarojini Nagar", 1300, 150, BL),             # 24
    {"name": "Yellow Line", "kind": "metro", "price": 2000, "rent": 250, "icon": "🚇"},  # 25
    prop("Janakpuri", 1700, 200, BL),                  # 26
    prop("Greater Kailash", 3500, 450, GD),            # 27
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
             dice=None, log=[], winner=None, anim=None)


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
    G["players"][pid] = {"name": name, "cash": START_CASH, "pos": 0, "sid": sid}
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
    path = [(old + k) % len(BOARD) for k in range(d1 + d2 + 1)]   # every tile hopped over
    p["pos"] = path[-1]
    log(f"🎲 {nm(pid)} rolled {d1}+{d2} → {BOARD[p['pos']]['name']}")
    if p["pos"] < old:
        move_money(BANK, pid, SALARY, "passed Start")
    resolve(pid)
    if p["pos"] != path[-1]:          # sent to Tihar by the naka
        path.append(p["pos"])
    G["anim"] = {"pid": pid, "path": path, "t0": time.time()}
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
.t .ic{font-size:13px;font-size:3.5cqw;line-height:1}

.mv{position:absolute;width:4.4%;aspect-ratio:1;margin:-2.2% 0 0 -2.2%;z-index:3;pointer-events:none}
.mv .tok{width:100%;height:100%}

.ctr{grid-row:2/8;grid-column:2/8;margin:1px;background:#c5d3dc;border-radius:8px;color:#333;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:3%;gap:1.5%;min-height:0;overflow:hidden}
.ctr .dice{font-size:30px;font-size:9cqw;line-height:1;display:block}
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
        if owner:
            oc = PCOL[S["order"].index(owner)]
            badge = f'<div class="pr" style="background:{oc};color:#fff">{price}</div>'
            ring = f"box-shadow:inset 0 0 0 2px {oc};"
        else:
            badge = f'<div class="pr">{price}</div>'
            ring = ""
        if t["kind"] == "prop":
            style = f"background:{t['color']};{'' if owner else 'opacity:.8;'}{ring}"
            return f'<div class="t" style="{place}{style}">{badge}<div>{name}</div></div>'
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
        dice = f'<div class="dice"{shake}>{DIE[d[0]]} {DIE[d[1]]}</div>'
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
    else:
        title, sub = f"{esc(players[S['turn']]['name'])}'s turn", "Roll the dice"
    lg = "".join(f"<div>{esc(x)}</div>" for x in S["log"])
    return (f'<div class="ctr">{dice}<div class="tt">{title}</div>'
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
    live = G["phase"] in ("roll", "buy")
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
    }


def ctl_key(S):
    """When this changes, the buttons under the board must be redrawn."""
    return (S["phase"], S["turn"], S["winner"], len(S["order"]))


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
        with st.form("join"):
            name = st.text_input("Your name")
            go = st.form_submit_button("Join game", use_container_width=True)
        if go and name.strip():
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
        else:
            c[0].button("🏆 Game over", disabled=True, use_container_width=True, key="wait")

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
        if st.button("Reset game (everyone leaves)", use_container_width=True, key="reset"):
            run_action(reset)


with LOCK:
    S0 = snapshot(me)
st.session_state.ctl_key = ctl_key(S0)
board_view(me)
controls(S0, me)
