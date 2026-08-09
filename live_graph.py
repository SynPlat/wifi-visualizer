import subprocess
import re
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from collections import deque
from datetime import datetime


MAX_POINTS = 30

times = deque(maxlen=MAX_POINTS)
rssi_history = deque(maxlen=MAX_POINTS)
noise_history = deque(maxlen=MAX_POINTS)


def get_wifi_info():

    result = subprocess.run(
        ["sudo", "-n", "/usr/bin/wdutil", "info"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return None, None

    output = result.stdout

    rssi_match = re.search(
        r"^\s*RSSI\s*:\s*(-?\d+)",
        output,
        re.MULTILINE
    )

    noise_match = re.search(
        r"^\s*Noise\s*:\s*(-?\d+)",
        output,
        re.MULTILINE
    )

    if not rssi_match or not noise_match:
        return None, None

    rssi = int(rssi_match.group(1))
    noise = int(noise_match.group(1))

    return rssi, noise


# Create window
fig, ax = plt.subplots(figsize=(11, 6))

rssi_line, = ax.plot(
    [],
    [],
    marker="o",
    label="Signal (RSSI)"
)

noise_line, = ax.plot(
    [],
    [],
    marker="o",
    label="Noise"
)


ax.set_title("📡 Wi-Fi Signal Monitor")

ax.set_xlabel("Time")

ax.set_ylabel("dBm")

ax.set_ylim(-100, -30)

ax.grid(True, alpha=0.3)

ax.legend()


def update(frame):

    rssi, noise = get_wifi_info()

    if rssi is None:
        return rssi_line, noise_line

    current_time = datetime.now().strftime("%H:%M:%S")

    times.append(current_time)
    rssi_history.append(rssi)
    noise_history.append(noise)

    x = range(len(times))

    rssi_line.set_data(
        x,
        rssi_history
    )

    noise_line.set_data(
        x,
        noise_history
    )

    # Keep graph moving
    ax.set_xlim(
        0,
        max(MAX_POINTS - 1, len(times) - 1)
    )

    # Show current readings in title
    ax.set_title(
        f"📡 Wi-Fi Monitor   "
        f"Signal: {rssi} dBm   "
        f"Noise: {noise} dBm"
    )

    # Update time labels
    ax.set_xticks(list(x))

    ax.set_xticklabels(
        list(times),
        rotation=45,
        ha="right"
    )

    return rssi_line, noise_line


animation = FuncAnimation(
    fig,
    update,
    interval=2000,
    cache_frame_data=False
)


plt.tight_layout()

plt.show()