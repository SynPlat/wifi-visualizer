import tkinter as tk
from tkinter import ttk
import subprocess
import os
import re
import threading
import time
from collections import deque, defaultdict

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


# ============================================================
# SETTINGS
# ============================================================

UPDATE_MS = 2000
PING_INTERVAL = 3
PING_HOST = "1.1.1.1"
MAX_POINTS = 30

signal_history = deque(maxlen=MAX_POINTS)
noise_history = deque(maxlen=MAX_POINTS)
elapsed_history = deque(maxlen=MAX_POINTS)

ping_history = deque(maxlen=30)
ping_total = 0
ping_success = 0
start_time = time.time()


# ============================================================
# WIFI DATA
# ============================================================

def get_wifi_info():
    """Read live Wi-Fi information on macOS.

    The app is normally started through the .command launcher with sudo.
    When already running as root, call wdutil directly instead of spawning
    another sudo process. If that fails, fall back to system_profiler so the
    dashboard can still identify the current Wi-Fi network.
    """
    commands = []

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        commands.append(["/usr/bin/wdutil", "info"])
    else:
        commands.append(["/usr/bin/sudo", "-n", "/usr/bin/wdutil", "info"])

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except Exception:
            continue

        if result.returncode != 0:
            continue

        # wdutil may write diagnostic text to stderr on some macOS versions.
        # Parse both streams so RSSI/Noise are never silently lost.
        output = (result.stdout or "") + "\n" + (result.stderr or "")

        def value(name):
            # Accept values such as "-55 dBm", "390.0 Mbps", and "5g161/80".
            patterns = [
                rf"^\s*{re.escape(name)}\s*:\s*(.+?)\s*$",
                rf"{re.escape(name)}\s*:\s*([^\r\n]+)",
            ]
            for pattern in patterns:
                m = re.search(pattern, output, re.MULTILINE | re.IGNORECASE)
                if m:
                    return m.group(1).strip()
            return "Unknown"

        data = {
            "rssi": value("RSSI"),
            "noise": value("Noise"),
            "channel": value("Channel"),
            "tx": value("Tx Rate"),
            "phy": value("PHY Mode"),
        }

        if data["rssi"] != "Unknown":
            return data

    # Last-resort fallback: identify the current network even if wdutil is
    # unavailable. Signal/noise remain Unknown rather than crashing the UI.
    try:
        result = subprocess.run(
            ["/usr/sbin/system_profiler", "SPAirPortDataType"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            output = result.stdout
            current = re.search(
                r"Current Network Information:\s*\n\s*([^\n:]+):",
                output,
            )
            channel = re.search(r"^\s*Channel:\s*(.+)$", output, re.MULTILINE)
            tx = re.search(r"^\s*Transmit Rate:\s*(.+)$", output, re.MULTILINE)
            phy = re.search(r"^\s*PHY Mode:\s*(.+)$", output, re.MULTILINE)

            if current:
                return {
                    "rssi": "Unknown",
                    "noise": "Unknown",
                    "channel": channel.group(1).strip() if channel else "Unknown",
                    "tx": tx.group(1).strip() if tx else "Unknown",
                    "phy": phy.group(1).strip() if phy else "Unknown",
                }
    except Exception:
        pass

    return None


# ============================================================
# SIGNAL HELPERS
# ============================================================

def signal_percent(rssi):
    if rssi >= -50:
        return 100
    if rssi >= -60:
        return int(85 + (rssi + 60) * 1.5)
    if rssi >= -70:
        return int(65 + (rssi + 70) * 2)
    if rssi >= -80:
        return int(40 + (rssi + 80) * 2.5)
    if rssi >= -90:
        return int(10 + (rssi + 90) * 3)
    return 0


def noise_percent(noise):
    if noise <= -90:
        return 100
    if noise <= -80:
        return int(70 + (-80 - noise) * 3)
    if noise <= -70:
        return int(40 + (-70 - noise) * 3)
    if noise <= -60:
        return int(10 + (-60 - noise) * 3)
    return 0


def snr_percent(snr):
    return max(0, min(100, int(snr / 30 * 100)))


def quality(rssi):
    if rssi >= -50:
        return "EXCELLENT", "🟢"
    if rssi >= -60:
        return "GOOD", "🟢"
    if rssi >= -70:
        return "FAIR", "🟡"
    if rssi >= -80:
        return "WEAK", "🟠"
    return "POOR", "🔴"


def snr_quality(snr):
    if snr >= 30:
        return "EXCELLENT", "🟢"
    if snr >= 20:
        return "GOOD", "🟢"
    if snr >= 10:
        return "FAIR", "🟡"
    return "POOR", "🔴"


def band_from_channel(channel):
    if channel.startswith("2g"):
        return "2.4 GHz"
    if channel.startswith("5g"):
        return "5 GHz"
    if channel.startswith("6g"):
        return "6 GHz"
    return "Unknown"


# ============================================================
# PING
# ============================================================

def ping_worker():
    global ping_total, ping_success

    while True:
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "1000", PING_HOST],
                capture_output=True,
                text=True,
                timeout=4
            )

            ping_total += 1

            if result.returncode == 0:
                match = re.search(
                    r"time[=<]([\d.]+)\s*ms",
                    result.stdout
                )
                if match:
                    ping_history.append(float(match.group(1)))
                    ping_success += 1

        except Exception:
            ping_total += 1

        time.sleep(PING_INTERVAL)


threading.Thread(target=ping_worker, daemon=True).start()


# ============================================================
# SCANNER
# ============================================================

def scan_networks():
    """Return nearby Wi-Fi networks using macOS system_profiler JSON.

    JSON is considerably more stable than parsing the human-readable output.
    A text parser is retained as a fallback for older macOS versions.
    """
    try:
        result = subprocess.run(
            ["/usr/sbin/system_profiler", "SPAirPortDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        result = None

    if result is not None and result.returncode == 0 and result.stdout.strip():
        try:
            import json
            payload = json.loads(result.stdout)
            found = []

            def walk(obj, in_other=False):
                if isinstance(obj, dict):
                    name = obj.get("_name") or obj.get("name")
                    local = in_other or name == "Other Local Wi-Fi Networks"

                    if local and name and name != "Other Local Wi-Fi Networks":
                        channel = str(obj.get("spchannel") or obj.get("channel") or "?")
                        signal = obj.get("spairport_signal_noise") or obj.get("signal_noise")
                        if isinstance(signal, str):
                            nums = re.findall(r"-?\d+", signal)
                            signal = int(nums[0]) if nums else None
                        elif isinstance(signal, (list, tuple)) and signal:
                            try:
                                signal = int(signal[0])
                            except Exception:
                                signal = None

                        security = obj.get("spsecurity") or obj.get("security") or "?"
                        band = "?"
                        if "2GHz" in channel or "2.4" in channel:
                            band = "2.4 GHz"
                        elif "5GHz" in channel or "5 GHz" in channel:
                            band = "5 GHz"
                        elif "6GHz" in channel or "6 GHz" in channel:
                            band = "6 GHz"

                        if signal is not None:
                            found.append({
                                "ssid": str(name),
                                "channel": channel,
                                "band": band,
                                "signal": signal,
                                "security": str(security),
                            })

                    for value in obj.values():
                        walk(value, local)

                elif isinstance(obj, list):
                    for value in obj:
                        walk(value, in_other)

            walk(payload)

            # De-duplicate networks that appear more than once in JSON.
            unique = {}
            for n in found:
                key = (n["ssid"], n["channel"], n["signal"])
                unique[key] = n
            if unique:
                return list(unique.values())
        except Exception:
            pass

    # Fallback for macOS versions whose JSON schema differs.
    try:
        result = subprocess.run(
            ["/usr/sbin/system_profiler", "SPAirPortDataType"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return []

    if result.returncode != 0:
        return []

    networks = []
    current = None
    inside = False

    for raw in result.stdout.splitlines():
        stripped = raw.strip()

        if stripped == "Other Local Wi-Fi Networks:":
            inside = True
            continue

        if not inside:
            continue

        if stripped.startswith("Current Network Information:"):
            break

        if (
            raw.startswith("        ")
            and stripped.endswith(":")
            and not stripped.startswith((
                "PHY Mode:", "Channel:", "Network Type:", "Security:",
                "Signal / Noise:", "Transmit Rate:",
            ))
        ):
            if current:
                networks.append(current)
            current = {
                "ssid": stripped[:-1], "channel": "?", "band": "?",
                "signal": None, "security": "?"
            }
            continue

        if current is None:
            continue

        if stripped.startswith("Channel:"):
            ch = stripped.split(":", 1)[1].strip()
            current["channel"] = ch
            if "2GHz" in ch:
                current["band"] = "2.4 GHz"
            elif "5GHz" in ch:
                current["band"] = "5 GHz"
            elif "6GHz" in ch:
                current["band"] = "6 GHz"
        elif stripped.startswith("Signal / Noise:"):
            nums = re.findall(r"-?\d+", stripped.split(":", 1)[1])
            if nums:
                current["signal"] = int(nums[0])
        elif stripped.startswith("Security:"):
            current["security"] = stripped.split(":", 1)[1].strip()

    if current:
        networks.append(current)

    return [n for n in networks if n["signal"] is not None]


def channel_analysis(networks):
    channels = defaultdict(list)

    for n in networks:
        match = re.search(r"\b(\d+)\b", n["channel"])
        if match:
            channels[int(match.group(1))].append(n)

    return channels


def recommended_channel(networks):
    channels = channel_analysis(networks)

    if not channels:
        return None

    scores = {}

    for ch, nets in channels.items():
        score = len(nets) * 5
        for n in nets:
            if n["signal"] >= -60:
                score += 6
            elif n["signal"] >= -70:
                score += 4
            elif n["signal"] >= -80:
                score += 2
        scores[ch] = score

    return min(scores, key=scores.get)



# ============================================================
# MODERN UI
# ============================================================

BG = "#0a0f14"
PANEL = "#111820"
PANEL_2 = "#151e27"
BORDER = "#25313c"
CYAN = "#00e5ff"
WHITE = "#f4f7fa"
MUTED = "#7f8c99"
GREEN = "#35d07f"
YELLOW = "#f5c451"
RED = "#ff5c69"
BLUE = "#4da3ff"

def card(parent, **kwargs):
    return tk.Frame(parent, bg=PANEL, highlightbackground=BORDER,
                    highlightthickness=1, bd=0, **kwargs)

def make_label(parent, text="", size=10, bold=False, fg=WHITE, bg=PANEL, **kwargs):
    return tk.Label(parent, text=text,
                    font=("Helvetica", size, "bold" if bold else "normal"),
                    fg=fg, bg=bg, **kwargs)

# ============================================================
# MAIN WINDOW
# ============================================================

root = tk.Tk()
root.title("Wi-Fi Visualizer")
root.geometry("1320x900")
root.minsize(1100, 760)
root.configure(bg=BG)

style = ttk.Style()
try:
    style.theme_use("clam")
except Exception:
    pass
style.configure("Modern.Treeview", background=PANEL_2, foreground=WHITE,
                fieldbackground=PANEL_2, borderwidth=0, rowheight=34,
                font=("Helvetica", 10))
style.configure("Modern.Treeview.Heading", background="#1c2732",
                foreground=CYAN, borderwidth=0, font=("Helvetica", 9, "bold"))
style.map("Modern.Treeview", background=[("selected", "#174b5b")],
          foreground=[("selected", WHITE)])

# Header
header = tk.Frame(root, bg=BG)
header.pack(fill="x", padx=28, pady=(24, 10))
titlebox = tk.Frame(header, bg=BG)
titlebox.pack(side="left")
tk.Label(titlebox, text="Wi-Fi Visualizer", font=("Helvetica", 28, "bold"),
         fg=WHITE, bg=BG).pack(anchor="w")
tk.Label(titlebox, text="REAL-TIME WIRELESS ANALYTICS",
         font=("Helvetica", 9, "bold"), fg=CYAN, bg=BG).pack(anchor="w", pady=(2,0))

def launch_scanner():
    open_scanner()

scan_button = tk.Button(
    header,
    text="SCAN WI-FI",
    command=launch_scanner,
    font=("Helvetica", 9, "bold"),
    fg=BG,
    bg=CYAN,
    activebackground="#55efff",
    activeforeground=BG,
    relief="flat",
    bd=0,
    padx=16,
    pady=8,
    cursor="hand2",
)
scan_button.pack(side="right", padx=(12, 0), pady=4)

status = tk.Label(header, text="●  CONNECTING", font=("Helvetica", 9, "bold"),
                  fg=YELLOW, bg=BG)
status.pack(side="right", pady=8)

# Metrics row
metrics = tk.Frame(root, bg=BG)
metrics.pack(fill="x", padx=28, pady=(2, 12))

def metric_card(title, subtitle):
    f = card(metrics)
    f.pack(side="left", fill="both", expand=True, padx=5)
    make_label(f, title.upper(), 8, True, MUTED).pack(anchor="w", padx=16, pady=(13,2))
    v = make_label(f, "--", 21, True, WHITE)
    v.pack(anchor="w", padx=16)
    make_label(f, subtitle, 8, False, MUTED).pack(anchor="w", padx=16, pady=(1,12))
    return v

signal_metric = metric_card("Signal", "dBm")
snr_metric = metric_card("SNR", "signal-to-noise")
channel_metric = metric_card("Channel", "current channel")
health_metric = metric_card("Health", "connection score")

# Main content
content = tk.Frame(root, bg=BG)
content.pack(fill="both", expand=True, padx=28, pady=(0, 12))

left = card(content, width=300)
left.pack(side="left", fill="y", padx=(0,10))
left.pack_propagate(False)

make_label(left, "CONNECTION", 11, True, CYAN).pack(anchor="w", padx=18, pady=(14,7))
info_values = {}

def add_info(name):
    row = tk.Frame(left, bg=PANEL)
    row.pack(fill="x", padx=18, pady=3)
    make_label(row, name, 8, True, MUTED).pack(anchor="w")
    v = make_label(row, "--", 12, True, WHITE)
    v.pack(anchor="w")
    info_values[name] = v

for name in ("Signal","Noise","SNR","Channel","Band","Tx Rate","Wi-Fi Standard"):
    add_info(name)

tk.Frame(left, bg=BORDER, height=1).pack(fill="x", padx=18, pady=8)
make_label(left, "SIGNAL QUALITY", 10, True, CYAN).pack(anchor="w", padx=18)
quality_label = make_label(left, "--", 18, True, WHITE)
quality_label.pack(anchor="w", padx=18, pady=(3,0))
snr_quality_label = make_label(left, "SNR: --", 9, True, MUTED)
snr_quality_label.pack(anchor="w", padx=18, pady=(2,7))

right = card(content)
right.pack(side="left", fill="both", expand=True)

gh = tk.Frame(right, bg=PANEL)
gh.pack(fill="x", padx=18, pady=(15,0))
make_label(gh, "SIGNAL HISTORY", 11, True, WHITE).pack(side="left")
make_label(gh, "LIVE", 8, True, CYAN).pack(side="right")

figure = Figure(figsize=(8,4.7), dpi=100)
figure.patch.set_facecolor(PANEL)
ax = figure.add_subplot(111)
ax.set_facecolor(PANEL)
ax.set_ylim(-100,-30)
ax.set_xlabel("Elapsed Time", color=MUTED, fontsize=8)
ax.set_ylabel("dBm", color=MUTED, fontsize=8)
ax.tick_params(colors=MUTED, labelsize=8)
ax.grid(True, alpha=0.12)
for spine in ax.spines.values():
    spine.set_color(BORDER)
signal_line, = ax.plot([], [], linewidth=2.5, label="Signal", color=CYAN)
noise_line, = ax.plot([], [], linewidth=2, label="Noise", color="#4d6375")
legend = ax.legend(loc="upper right", frameon=False)
for t in legend.get_texts():
    t.set_color(WHITE)
canvas = FigureCanvasTkAgg(figure, master=right)
canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=10)

# Bottom cards
bottom = tk.Frame(root, bg=BG)
bottom.pack(fill="x", padx=28, pady=(0,12))
performance = card(bottom); performance.pack(side="left", fill="both", expand=True, padx=(0,5))
analysis = card(bottom); analysis.pack(side="left", fill="both", expand=True, padx=(5,0))

make_label(performance, "NETWORK PERFORMANCE", 9, True, CYAN).pack(anchor="w", padx=15, pady=(12,3))
performance_label = make_label(performance, "Testing internet latency...", 11, True, WHITE)
performance_label.pack(anchor="w", padx=15, pady=(0,12))

make_label(analysis, "CONNECTION ANALYSIS", 9, True, CYAN).pack(anchor="w", padx=15, pady=(12,3))
health_label = make_label(analysis, "Health Score: --", 12, True, WHITE)
health_label.pack(anchor="w", padx=15)
details_label = make_label(analysis, "Signal --%  •  Noise --%  •  SNR --%  •  Stability --%", 8, False, MUTED)
details_label.pack(anchor="w", padx=15, pady=(2,0))
recommendation_label = make_label(analysis, "Analyzing...", 8, True, WHITE)
recommendation_label.pack(anchor="w", padx=15, pady=(2,11))

def show_channel_chart(parent, networks, current_channel=None):
    channels = channel_analysis(networks)
    if not channels:
        make_label(parent, "No channel data available", 10, True, MUTED, PANEL).pack(pady=20)
        return
    rows = sorted(channels.items())
    nums = [c for c,_ in rows]; counts=[len(n) for _,n in rows]
    fig=Figure(figsize=(8.8,2.35),dpi=90); fig.patch.set_facecolor(PANEL)
    a=fig.add_subplot(111); a.set_facecolor(PANEL)
    current_num=None
    if current_channel:
        m=re.search(r"g(\d+)",str(current_channel),re.I) or re.search(r"\b(\d+)\b",str(current_channel))
        if m: current_num=int(m.group(1))
    x=list(range(len(nums)))
    a.bar(x,counts,color=[CYAN if c==current_num else "#287FB8" for c in nums],width=.65)
    a.set_xticks(x); a.set_xticklabels([str(c) for c in nums])
    a.tick_params(axis="x",colors=WHITE,labelsize=8); a.tick_params(axis="y",colors=MUTED,labelsize=8)
    a.set_ylabel("Networks",color=MUTED,fontsize=8); a.set_xlabel("Wi-Fi Channel",color=MUTED,fontsize=8)
    a.grid(axis="y",alpha=.12)
    for s in a.spines.values(): s.set_color(BORDER)
    if current_num in nums:
        i=nums.index(current_num); a.set_ylim(0,max(counts)+1.2)
        a.annotate("YOU",(i,counts[i]),xytext=(0,12),textcoords="offset points",
                   ha="center",color=CYAN,fontsize=8,fontweight="bold",
                   arrowprops=dict(arrowstyle="->",color=CYAN,lw=1.3))
    fig.tight_layout(pad=1)
    c=FigureCanvasTkAgg(fig,master=parent); c.get_tk_widget().pack(fill="x",padx=14,pady=(0,10))
    return c

def open_scanner():
    win=tk.Toplevel(root); win.title("Wi-Fi Environment"); win.geometry("1100x820"); win.configure(bg=BG)
    bar=tk.Frame(win,bg=BG); bar.pack(fill="x",padx=24,pady=(22,10))
    tk.Label(bar,text="Wi-Fi Environment",font=("Helvetica",22,"bold"),fg=WHITE,bg=BG).pack(side="left")
    result_label=tk.Label(bar,text="Ready to scan",font=("Helvetica",10,"bold"),fg=CYAN,bg=BG); result_label.pack(side="right")
    container=card(win); container.pack(fill="both",expand=True,padx=24,pady=8)
    cols=("ssid","channel","band","signal","security")
    tree=ttk.Treeview(container,columns=cols,show="headings",style="Modern.Treeview")
    heads={"ssid":"NETWORK","channel":"CHANNEL","band":"BAND","signal":"SIGNAL","security":"SECURITY"}
    widths={"ssid":330,"channel":140,"band":150,"signal":150,"security":220}
    for c in cols: tree.heading(c,text=heads[c]); tree.column(c,width=widths[c],anchor="center")
    tree.pack(fill="both",expand=True,padx=12,pady=12)
    chart_card=card(win); chart_card.pack(fill="x",padx=24,pady=(0,8))
    make_label(chart_card,"CHANNEL CONGESTION",10,True,CYAN).pack(anchor="w",padx=14,pady=(10,2))
    chart_frame=tk.Frame(chart_card,bg=PANEL); chart_frame.pack(fill="x")
    def do_scan():
        result_label.config(text="SCANNING..."); win.update_idletasks()
        for i in tree.get_children(): tree.delete(i)
        nets=scan_networks()
        for n in nets:
            tree.insert("", "end", values=(n["ssid"],n["channel"],n["band"],f"{n['signal']} dBm",n["security"]))
        for w in chart_frame.winfo_children(): w.destroy()
        current=None
        try:
            d=get_wifi_info()
            if d: current=d.get("channel")
        except Exception: pass
        show_channel_chart(chart_frame,nets,current)
        rec=recommended_channel(nets)
        if rec is not None:
            count=len(channel_analysis(nets)[rec])
            state="LOW" if count<=1 else ("MEDIUM" if count==2 else "HIGH")
            result_label.config(text=f"RECOMMENDED CHANNEL  {rec}   •   {state} CONGESTION   •   {len(nets)} NETWORKS")
        else: result_label.config(text=f"{len(nets)} NETWORKS FOUND")
    tk.Button(win,text="SCAN NEARBY NETWORKS",command=do_scan,font=("Helvetica",10,"bold"),
              fg=BG,bg=CYAN,activebackground="#55efff",activeforeground=BG,relief="flat",
              bd=0,padx=24,pady=10,cursor="hand2").pack(pady=(4,18))
    do_scan()

def update_dashboard():
    data=get_wifi_info()
    if not data:
        status.config(text="●  WIFI UNAVAILABLE",fg=RED)
        root.after(UPDATE_MS,update_dashboard); return
    rssi_match = re.search(r"-\d+", str(data.get("rssi", "")))
    noise_match = re.search(r"-\d+", str(data.get("noise", "")))

    # Never fake a -100 dBm reading. If parsing fails, keep the last real value.
    if rssi_match:
        rssi = int(rssi_match.group(0))
    elif signal_history:
        rssi = signal_history[-1]
    else:
        status.config(text="●  READING WIFI...", fg=YELLOW)
        root.after(UPDATE_MS, update_dashboard)
        return

    if noise_match:
        noise = int(noise_match.group(0))
    elif noise_history:
        noise = noise_history[-1]
    else:
        noise = -90
    snr=rssi-noise; elapsed=int(time.time()-start_time)
    signal_history.append(rssi); noise_history.append(noise); elapsed_history.append(elapsed)
    sp=max(0,min(100,signal_percent(rssi))); np=max(0,min(100,noise_percent(noise))); snrp=snr_percent(snr)
    if len(signal_history)>=2:
        variation=max(signal_history)-min(signal_history); stability=max(0,min(100,100-variation*6))
    else: stability=100
    health=int(sp*.35+np*.20+snrp*.30+stability*.15)
    q,icon=quality(rssi); sq,sicon=snr_quality(snr)
    signal_metric.config(text=f"{rssi} dBm"); snr_metric.config(text=f"{snr} dB")
    channel_metric.config(text=data["channel"]); health_metric.config(text=f"{health}/100")
    info_values["Signal"].config(text=f"{rssi} dBm"); info_values["Noise"].config(text=f"{noise} dBm")
    info_values["SNR"].config(text=f"{snr} dB"); info_values["Channel"].config(text=data["channel"])
    info_values["Band"].config(text=band_from_channel(data["channel"]))
    info_values["Tx Rate"].config(text=data["tx"]); info_values["Wi-Fi Standard"].config(text=data["phy"])
    quality_label.config(text=f"{icon} {q}"); snr_quality_label.config(text=f"{sicon} SNR {snr} dB  •  {sq}")
    x=list(range(len(signal_history))); signal_line.set_data(x,list(signal_history)); noise_line.set_data(x,list(noise_history))
    ax.set_xlim(0,max(10,len(signal_history)-1))
    if len(elapsed_history)>1:
        pos=list(range(0,len(elapsed_history),max(1,len(elapsed_history)//5)))
        if pos[-1]!=len(elapsed_history)-1: pos.append(len(elapsed_history)-1)
        ax.set_xticks(pos); ax.set_xticklabels([f"{list(elapsed_history)[i]}s" for i in pos],color=MUTED)
    canvas.draw_idle()
    if ping_history:
        current=ping_history[-1]; average=sum(ping_history)/len(ping_history)
        jitter=(sum(abs(ping_history[i]-ping_history[i-1]) for i in range(1,len(ping_history)))/(len(ping_history)-1)) if len(ping_history)>=2 else 0
        loss=(max(0,1-ping_success/ping_total)*100) if ping_total else 0
        performance_label.config(text=f"●  Ping {current:.1f} ms   •   Avg {average:.1f} ms   •   Jitter {jitter:.1f} ms   •   Loss {loss:.1f}%")
    else: performance_label.config(text="Testing internet latency...")
    health_icon="●"; msg="Good wireless conditions" if health>=75 else ("Connection is acceptable" if health>=50 else "Poor wireless conditions")
    health_label.config(text=f"{health_icon}  Health Score: {health}/100")
    details_label.config(text=f"Signal {sp}%  •  Noise {np}%  •  SNR {snrp}%  •  Stability {stability}%")
    recommendation_label.config(text=f"{health_icon}  {msg}")
    status.config(text="●  CONNECTED",fg=GREEN)
    root.after(UPDATE_MS,update_dashboard)

update_dashboard()
root.mainloop()
# Wi-Fi Visualizer v1.2.4 - verified Wi-Fi detection
