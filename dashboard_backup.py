import tkinter as tk
from tkinter import ttk
import subprocess
import re
from collections import deque
from datetime import datetime

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.animation import FuncAnimation


# ============================================================
# SETTINGS
# ============================================================

MAX_POINTS = 30

signal_history = deque(maxlen=MAX_POINTS)
noise_history = deque(maxlen=MAX_POINTS)
time_history = deque(maxlen=MAX_POINTS)


# ============================================================
# GET WI-FI INFORMATION
# ============================================================

def get_wifi_info():

    result = subprocess.run(
        ["sudo", "-n", "/usr/bin/wdutil", "info"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return None

    output = result.stdout

    def get_value(name):

        match = re.search(
            rf"^\s*{re.escape(name)}\s*:\s*(.+)$",
            output,
            re.MULTILINE
        )

        return match.group(1).strip() if match else "Unknown"

    return {
        "rssi": get_value("RSSI"),
        "noise": get_value("Noise"),
        "channel": get_value("Channel"),
        "tx_rate": get_value("Tx Rate"),
        "phy": get_value("PHY Mode"),
        "ssid": get_value("SSID")
    }


# ============================================================
# SIGNAL QUALITY
# ============================================================

def get_signal_quality(rssi):

    if rssi >= -50:
        return "EXCELLENT", "🟢"

    elif rssi >= -60:
        return "GOOD", "🟢"

    elif rssi >= -70:
        return "FAIR", "🟡"

    else:
        return "POOR", "🔴"


# ============================================================
# BAND DETECTION
# ============================================================

def get_band(channel):

    if channel.startswith("2g"):
        return "2.4 GHz"

    elif channel.startswith("5g"):
        return "5 GHz"

    elif channel.startswith("6g"):
        return "6 GHz"

    return "Unknown"


# ============================================================
# MAIN WINDOW
# ============================================================

root = tk.Tk()

root.title("📡 Wi-Fi Visualizer")

root.geometry("1100x700")

root.configure(bg="#101418")


# ============================================================
# TITLE
# ============================================================

title = tk.Label(
    root,
    text="📡 WI-FI VISUALIZER",
    font=("Helvetica", 24, "bold"),
    fg="#00e5ff",
    bg="#101418"
)

title.pack(pady=(20, 10))


status_label = tk.Label(
    root,
    text="Connecting...",
    font=("Helvetica", 11),
    fg="#aaaaaa",
    bg="#101418"
)

status_label.pack()


# ============================================================
# MAIN CONTENT
# ============================================================

content = tk.Frame(
    root,
    bg="#101418"
)

content.pack(
    fill="both",
    expand=True,
    padx=20,
    pady=20
)


# ============================================================
# CONNECTION PANEL
# ============================================================

connection_frame = tk.LabelFrame(
    content,
    text=" CONNECTION ",
    font=("Helvetica", 12, "bold"),
    fg="#00e5ff",
    bg="#151b21",
    bd=2
)

connection_frame.pack(
    side="left",
    fill="y",
    padx=(0, 15)
)


def create_info_label(parent, title):

    frame = tk.Frame(
        parent,
        bg="#151b21"
    )

    frame.pack(
        fill="x",
        padx=20,
        pady=8
    )

    label_title = tk.Label(
        frame,
        text=title,
        font=("Helvetica", 10),
        fg="#888888",
        bg="#151b21"
    )

    label_title.pack(anchor="w")

    value = tk.Label(
        frame,
        text="--",
        font=("Helvetica", 14, "bold"),
        fg="white",
        bg="#151b21"
    )

    value.pack(anchor="w")

    return value


signal_label = create_info_label(
    connection_frame,
    "Signal"
)

noise_label = create_info_label(
    connection_frame,
    "Noise"
)

channel_label = create_info_label(
    connection_frame,
    "Channel"
)

band_label = create_info_label(
    connection_frame,
    "Band"
)

tx_label = create_info_label(
    connection_frame,
    "Tx Rate"
)

phy_label = create_info_label(
    connection_frame,
    "Wi-Fi Standard"
)


# ============================================================
# GRAPH
# ============================================================

graph_frame = tk.Frame(
    content,
    bg="#151b21"
)

graph_frame.pack(
    side="right",
    fill="both",
    expand=True
)


figure, ax = plt.subplots(
    figsize=(7, 5),
    dpi=100
)

figure.patch.set_facecolor("#151b21")

ax.set_facecolor("#151b21")

ax.tick_params(
    colors="white"
)

ax.xaxis.label.set_color("white")

ax.yaxis.label.set_color("white")

ax.set_ylabel("dBm")

ax.set_xlabel("Time")

ax.set_ylim(-100, -30)

ax.grid(
    True,
    alpha=0.2
)

for spine in ax.spines.values():
    spine.set_color("#444444")


signal_line, = ax.plot(
    [],
    [],
    linewidth=2,
    marker="o",
    label="Signal"
)

noise_line, = ax.plot(
    [],
    [],
    linewidth=2,
    marker="o",
    label="Noise"
)

legend = ax.legend()

legend.get_frame().set_facecolor("#151b21")

for text in legend.get_texts():
    text.set_color("white")


canvas = FigureCanvasTkAgg(
    figure,
    master=graph_frame
)

canvas.get_tk_widget().pack(
    fill="both",
    expand=True
)


# ============================================================
# STATUS PANEL
# ============================================================

analysis_frame = tk.Frame(
    root,
    bg="#151b21"
)

analysis_frame.pack(
    fill="x",
    padx=20,
    pady=(0, 20)
)


analysis_label = tk.Label(
    analysis_frame,
    text="Connection analysis",
    font=("Helvetica", 12, "bold"),
    fg="#00e5ff",
    bg="#151b21"
)

analysis_label.pack(
    anchor="w",
    padx=15,
    pady=(10, 5)
)


quality_label = tk.Label(
    analysis_frame,
    text="Checking connection...",
    font=("Helvetica", 12),
    fg="white",
    bg="#151b21"
)

quality_label.pack(
    anchor="w",
    padx=15,
    pady=(0, 10)
)


# ============================================================
# UPDATE DASHBOARD
# ============================================================

def update_dashboard():

    info = get_wifi_info()

    if info is None:

        status_label.config(
            text="❌ Unable to read Wi-Fi information"
        )

        root.after(
            2000,
            update_dashboard
        )

        return


    # --------------------------------------------------------
    # Convert values
    # --------------------------------------------------------

    try:
        rssi = int(
            info["rssi"].split()[0]
        )

    except:
        rssi = -100


    try:
        noise = int(
            info["noise"].split()[0]
        )

    except:
        noise = -100


    # --------------------------------------------------------
    # Signal quality
    # --------------------------------------------------------

    quality, icon = get_signal_quality(
        rssi
    )


    # --------------------------------------------------------
    # Band
    # --------------------------------------------------------

    band = get_band(
        info["channel"]
    )


    # --------------------------------------------------------
    # Update labels
    # --------------------------------------------------------

    signal_label.config(
        text=f"{rssi} dBm"
    )

    noise_label.config(
        text=f"{noise} dBm"
    )

    channel_label.config(
        text=info["channel"]
    )

    band_label.config(
        text=band
    )

    tx_label.config(
        text=info["tx_rate"]
    )

    phy_label.config(
        text=info["phy"]
    )


    status_label.config(
        text=f"Connected • Updated {datetime.now().strftime('%H:%M:%S')}"
    )


    quality_label.config(
        text=f"{icon} Signal: {quality}     •     🟢 Noise: {'EXCELLENT' if noise <= -85 else 'GOOD'}"
    )


    # --------------------------------------------------------
    # Add graph data
    # --------------------------------------------------------

    signal_history.append(
        rssi
    )

    noise_history.append(
        noise
    )

    time_history.append(
        datetime.now().strftime("%H:%M:%S")
    )


    x = range(
        len(signal_history)
    )


    signal_line.set_data(
        x,
        signal_history
    )

    noise_line.set_data(
        x,
        noise_history
    )


    ax.set_xlim(
        0,
        max(
            MAX_POINTS - 1,
            len(signal_history) - 1
        )
    )


    ax.set_xticks(
        list(x)
    )

    ax.set_xticklabels(
        list(time_history),
        rotation=45,
        ha="right",
        color="white"
    )


    canvas.draw_idle()


    # Update every 2 seconds

    root.after(
        2000,
        update_dashboard
    )


# ============================================================
# START
# ============================================================

sudo_check = subprocess.run(
    ["sudo", "-n", "/usr/bin/wdutil", "info"],
    capture_output=True
)

if sudo_check.returncode != 0:

    status_label.config(
        text="⚠️ Run 'sudo -v' in Terminal first"
    )

else:

    update_dashboard()


root.mainloop()