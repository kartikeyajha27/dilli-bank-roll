import threading
import uuid

import streamlit as st

BANK = "BANK"
MAX_PLAYERS = 4
START_CASH = 15_000
BANK_START = 500_000
SALARY = 2_000

# Delhi properties: name -> (price, rent)
PROPERTIES = {
    "Connaught Place": (3000, 400),
    "Defence Colony": (3200, 420),
    "Vasant Kunj": (2800, 350),
    "Hauz Khas Village": (2600, 320),
    "Saket": (2400, 300),
    "Lajpat Nagar": (2200, 280),
    "Nehru Place": (2000, 250),
    "Chandni Chowk": (1800, 220),
    "Pitampura": (1500, 180),
    "Karol Bagh": (1600, 200),
    "Dwarka": (1400, 160),
    "Rohini": (1200, 140),
}


@st.cache_resource
def get_game():
    """One shared state object for the whole server process (all phones)."""
    return {
        "lock": threading.Lock(),
        "players": {},   # pid -> {"name": str, "cash": int}
        "order": [],     # pids in turn order
        "owner": {},     # property -> pid
        "bank": BANK_START,
        "turn": 0,
        "log": [],
    }


G = get_game()
LOCK = G["lock"]


# ---------- helpers (call with LOCK held) ----------

def cash_of(x):
    return G["bank"] if x == BANK else G["players"][x]["cash"]


def add_cash(x, delta):
    if x == BANK:
        G["bank"] += delta
    else:
        G["players"][x]["cash"] += delta


def name_of(x):
    return "Bank" if x == BANK else G["players"][x]["name"]


def log(msg):
    G["log"].insert(0, msg)
    del G["log"][50:]


def move_money(src, dst, amt, note):
    if amt <= 0:
        return "Enter an amount above zero."
    if cash_of(src) < amt:
        return f"{name_of(src)} doesn't have enough cash."
    add_cash(src, -amt)
    add_cash(dst, amt)
    log(f"{name_of(src)} → {name_of(dst)}: ₹{amt:,} ({note})")
    return None


def buy_property(pid, prop):
    if prop in G["owner"]:
        return f"{prop} is already owned by {name_of(G['owner'][prop])}."
    price, _ = PROPERTIES[prop]
    err = move_money(pid, BANK, price, f"bought {prop}")
    if err:
        return err
    G["owner"][prop] = pid
    log(f"{name_of(pid)} now owns {prop}")
    return None


def buy_from_player(buyer, seller, prop, price):
    if G["owner"].get(prop) != seller:
        return f"{name_of(seller)} doesn't own {prop}."
    err = move_money(buyer, seller, price, f"{prop} bought from player")
    if err:
        return err
    G["owner"][prop] = buyer
    log(f"{name_of(buyer)} took {prop} from {name_of(seller)}")
    return None


def pay_rent(pid, prop):
    owner = G["owner"].get(prop)
    if owner is None:
        return f"{prop} is unowned. Buy it first."
    if owner == pid:
        return "You own this one, so no rent."
    _, rent = PROPERTIES[prop]
    return move_money(pid, owner, rent, f"rent for {prop}")


def end_turn():
    if G["order"]:
        G["turn"] = (G["turn"] + 1) % len(G["order"])
        log(f"Turn passed to {name_of(G['order'][G['turn']])}")
    return None


def join(name):
    if len(G["players"]) >= MAX_PLAYERS:
        return None
    pid = uuid.uuid4().hex[:8]
    G["players"][pid] = {"name": name, "cash": 0}
    G["order"].append(pid)
    move_money(BANK, pid, START_CASH, "starting cash")
    log(f"{name} joined the game")
    return pid


def run_action(fn, *args):
    with LOCK:
        err = fn(*args)
    if err:
        st.toast(f"⚠️ {err}")
    else:
        st.toast("✅ Done")


# ---------- UI ----------

st.set_page_config(page_title="Dilli Bank Roll", page_icon="🏛️")
st.title("🏛️ Dilli Bank Roll")

me = st.query_params.get("p")
with LOCK:
    joined = me in G["players"]
    seats_full = len(G["players"]) >= MAX_PLAYERS

if not joined:
    if seats_full:
        st.warning("All 4 seats are taken.")
        st.stop()

    st.subheader("Take a seat")
    with st.form("join"):
        name = st.text_input("Your name")
        submitted = st.form_submit_button("Join")
    if submitted and name.strip():
        with LOCK:
            new_pid = join(name.strip())
        if new_pid is None:
            st.error("Game is full.")
            st.stop()
        st.query_params["p"] = new_pid   # identity survives page refresh
        st.rerun()
    st.stop()


@st.fragment(run_every=3)   # auto-refresh this phone's view every 3 seconds
def game_view(me):
    with LOCK:
        order = list(G["order"])
        players = {p: dict(G["players"][p]) for p in order}
        owners = dict(G["owner"])
        bank = G["bank"]
        turn_pid = order[G["turn"]] if order else None
        logs = list(G["log"][:8])

    my_cash = players[me]["cash"]
    my_props = [p for p, o in owners.items() if o == me]

    c1, c2 = st.columns(2)
    c1.metric("Your cash", f"₹{my_cash:,}")
    c2.metric("Bank reserve", f"₹{bank:,}")
    st.info(f"🎲 Turn: **{players[turn_pid]['name']}**" + ("  (you)" if turn_pid == me else ""))

    st.markdown("**Players**")
    st.dataframe(
        [
            {
                "Player": ("➡️ " if p == turn_pid else "") + players[p]["name"] + (" (you)" if p == me else ""),
                "Cash": f"₹{players[p]['cash']:,}",
                "Properties": sum(1 for o in owners.values() if o == p),
            }
            for p in order
        ],
        hide_index=True,
        use_container_width=True,
    )

    st.markdown("**Properties**")
    st.dataframe(
        [
            {
                "Property": prop,
                "Price": f"₹{price:,}",
                "Rent": f"₹{rent:,}",
                "Owner": players[owners[prop]]["name"] if prop in owners else "Unowned",
            }
            for prop, (price, rent) in PROPERTIES.items()
        ],
        hide_index=True,
        use_container_width=True,
    )

    st.markdown("**Your move**")
    others = {p: players[p]["name"] for p in order if p != me}
    amt = st.number_input("Amount (₹)", min_value=0, step=100, value=0, key="amt")
    prop = st.selectbox("Property", list(PROPERTIES), key="prop")

    b1, b2 = st.columns(2)
    if b1.button(f"Collect salary ₹{SALARY:,}", use_container_width=True):
        run_action(move_money, BANK, me, SALARY, "salary")
    if b2.button("Pay bank", use_container_width=True):
        run_action(move_money, me, BANK, amt, "bank payment")
    if b1.button("Buy property from bank", use_container_width=True):
        run_action(buy_property, me, prop)
    if b2.button("Pay rent", use_container_width=True):
        run_action(pay_rent, me, prop)

    if others:
        st.markdown("**With another player**")
        target = st.selectbox(
            "Player", list(others), format_func=lambda x: others[x], key="target"
        )
        p1, p2 = st.columns(2)
        if p1.button("Pay player", use_container_width=True):
            run_action(move_money, me, target, amt, "payment")
        if p2.button("Buy property from player", use_container_width=True):
            run_action(buy_from_player, me, target, prop, amt)

    if st.button("End my turn", use_container_width=True, type="primary"):
        run_action(end_turn)

    st.markdown("**Recent activity**")
    st.dataframe(
        [{"Event": line} for line in logs] or [{"Event": "No activity yet"}],
        hide_index=True,
        use_container_width=True,
    )


game_view(me)
