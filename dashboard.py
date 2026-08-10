import tkinter as tk
from tkinter import ttk, filedialog
import subprocess
import os

# Always resolve project-relative files from the dashboard's own location.
# This makes direct Python launches and bundled .app launches consistent.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
import re
import threading
import time
import sys
import shlex
import csv
from collections import deque, defaultdict


# ============================================================
# MACOS ADMIN ELEVATION
# ============================================================

def ensure_admin():
    # The .command launcher already runs the app with sudo.
    # When the .app is double-clicked in Finder, macOS does not give it
    # administrator privileges. Ask macOS for permission and relaunch
    # this exact executable as root.
    if sys.platform != "darwin":
        return

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return

    executable = shlex.quote(os.path.abspath(sys.executable))

    # Escape the shell command for an AppleScript string literal.
    shell_command = f"exec {executable}"
    applescript_string = shell_command.replace("\\\\", "\\\\\\\\").replace('"', '\\\\\\"')

    script = (
        f'do shell script "{applescript_string}" '
        f'with administrator privileges'
    )

    try:
        subprocess.Popen(
            ["/usr/bin/osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    sys.exit(0)


ensure_admin()

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
    """Read live Wi-Fi information on macOS with multiple fallbacks.

    macOS versions differ in what wdutil exposes to a bundled/root process.
    Try wdutil first, then networksetup/system_profiler so the dashboard can
    still detect Wi-Fi when one diagnostic interface is unavailable.
    """

    def parse_value(output, *names):
        for name in names:
            patterns = [
                rf"^\s*{re.escape(name)}\s*:\s*(.+?)\s*$",
                rf"{re.escape(name)}\s*:\s*([^\r\n]+)",
            ]
            for pattern in patterns:
                match = re.search(pattern, output, re.MULTILINE | re.IGNORECASE)
                if match:
                    value = match.group(1).strip()
                    if value:
                        return value
        return "Unknown"

    def run_cmd(command, timeout=8):
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout
            )
        except Exception:
            return None

    # Find the actual Wi-Fi device instead of assuming en0.
    interface = None
    ports = run_cmd(
        ["/usr/sbin/networksetup", "-listallhardwareports"],
        timeout=5
    )
    if ports and ports.returncode == 0:
        port_lines = ports.stdout.splitlines()
        for i, line in enumerate(port_lines):
            if line.strip().lower() in ("hardware port: wi-fi",
                                        "hardware port: airport"):
                if i + 1 < len(port_lines):
                    match = re.search(r"Device:\s*(\S+)", port_lines[i + 1])
                    if match:
                        interface = match.group(1)
                        break

    if not interface:
        interface = "en0"

    # ------------------------------------------------------------
    # 1. wdutil — best source when available.
    # ------------------------------------------------------------
    commands = []
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        commands.append(["/usr/bin/wdutil", "info"])
    else:
        commands.append(["/usr/bin/sudo", "-n", "/usr/bin/wdutil", "info"])

    for command in commands:
        result = run_cmd(command)
        if not result or result.returncode != 0:
            continue

        output = (result.stdout or "") + "\n" + (result.stderr or "")

        data = {
            "rssi": parse_value(output, "RSSI"),
            "noise": parse_value(output, "Noise"),
            "channel": parse_value(output, "Channel"),
            "tx": parse_value(output, "Tx Rate", "Transmit Rate"),
            "phy": parse_value(output, "PHY Mode", "PHY"),
            "ssid": parse_value(output, "SSID", "BSSID"),
            "interface": interface,
        }

        # wdutil may expose only some fields. If it gives us RSSI, it's a
        # valid live Wi-Fi source.
        if data["rssi"] != "Unknown":
            return data

    # ------------------------------------------------------------
    # 2. networksetup — reliable for current Wi-Fi network/device.
    # ------------------------------------------------------------
    network_name = None
    airport = run_cmd(
        ["/usr/sbin/networksetup", "-getairportnetwork", interface],
        timeout=5
    )
    if airport and airport.returncode == 0:
        match = re.search(
            r"Current Wi-Fi Network:\s*(.+)",
            airport.stdout or "",
            re.IGNORECASE
        )
        if match:
            network_name = match.group(1).strip()

    info = run_cmd(
        ["/usr/sbin/networksetup", "-getinfo", "Wi-Fi"],
        timeout=5
    )
    info_text = (info.stdout if info and info.returncode == 0 else "") or ""

    # ------------------------------------------------------------
    # 3. system_profiler — fallback for signal/channel/PHY data.
    # ------------------------------------------------------------
    result = run_cmd(
        ["/usr/sbin/system_profiler", "SPAirPortDataType"],
        timeout=12
    )
    if result and result.returncode == 0:
        output = result.stdout or ""

        # Current Network Information is followed by the SSID as an
        # indented key. Avoid the malformed regex from the old version.
        current_match = re.search(
            r"Current Network Information:\s*\n\s*([^:\n]+):",
            output,
            re.IGNORECASE
        )
        current_ssid = (
            current_match.group(1).strip()
            if current_match else network_name
        )

        channel_match = re.search(
            r"^\s*Channel:\s*(.+)$", output, re.MULTILINE
        )
        tx_match = re.search(
            r"^\s*Transmit Rate:\s*(.+)$", output, re.MULTILINE
        )
        phy_match = re.search(
            r"^\s*PHY Mode:\s*(.+)$", output, re.MULTILINE
        )
        rssi_match = re.search(
            r"^\s*(?:RSSI|Signal / Noise):\s*(-?\d+)",
            output, re.MULTILINE | re.IGNORECASE
        )
        noise_match = re.search(
            r"^\s*Noise:\s*(-?\d+)",
            output, re.MULTILINE | re.IGNORECASE
        )

        if current_ssid or network_name or rssi_match:
            return {
                "rssi": (
                    rssi_match.group(1) + " dBm"
                    if rssi_match else "Unknown"
                ),
                "noise": (
                    noise_match.group(1) + " dBm"
                    if noise_match else "Unknown"
                ),
                "channel": (
                    channel_match.group(1).strip()
                    if channel_match else "Unknown"
                ),
                "tx": (
                    tx_match.group(1).strip()
                    if tx_match else "Unknown"
                ),
                "phy": (
                    phy_match.group(1).strip()
                    if phy_match else "Unknown"
                ),
                "ssid": current_ssid or network_name or "Unknown",
                "interface": interface,
            }

    # ------------------------------------------------------------
    # 4. Last fallback: networksetup can still prove Wi-Fi is connected.
    # ------------------------------------------------------------
    connected = (
        network_name
        and network_name.lower() not in (
            "you are not associated with an airPort network.",
            "you are not associated with a wi-fi network."
        )
    )

    if connected:
        return {
            "rssi": "Unknown",
            "noise": "Unknown",
            "channel": "Unknown",
            "tx": "Unknown",
            "phy": "Unknown",
            "ssid": network_name,
            "interface": interface,
        }

    # If Wi-Fi is not connected, return None so the dashboard can clearly
    # report that state rather than displaying fake readings.
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
# NETWORK TOOLS
# ============================================================

def get_wifi_interface():
    try:
        result=subprocess.run(
            ["/usr/sbin/networksetup","-listallhardwareports"],
            capture_output=True,text=True,timeout=5)
        lines=result.stdout.splitlines()
        for i,line in enumerate(lines):
            if line.strip()=="Hardware Port: Wi-Fi" and i+1<len(lines):
                m=re.search(r"Device:\s*(\S+)",lines[i+1])
                if m: return m.group(1)
    except Exception:
        pass
    return None

def run_ping_test(host="1.1.1.1", count=5):
    """Return real ICMP latency and packet-loss statistics."""
    try:
        result = subprocess.run(
            ["/sbin/ping", "-c", str(count), "-W", "1000", host],
            capture_output=True, text=True, timeout=15
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")

        loss = 100.0
        m = re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", output, re.I)
        if m:
            loss = float(m.group(1))

        # macOS ping summary: round-trip min/avg/max/stddev = 8.1/9.2/...
        avg = None
        jitter = None
        m = re.search(
            r"round-trip.*?=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms",
            output, re.I | re.S
        )
        if m:
            avg = float(m.group(2))
            jitter = float(m.group(4))

        return {
            "ping": avg,
            "loss": loss,
            "jitter": jitter,
            "raw": output
        }
    except subprocess.TimeoutExpired:
        return {"ping": None, "loss": 100.0, "raw": "Ping timed out."}
    except Exception as exc:
        return {"ping": None, "loss": None, "raw": str(exc)}


def _parse_capacity(value):
    m = re.search(r"([\d.]+)\s*(Gbps|Mbps|Kbps)", value, re.I)
    if not m:
        return None
    number = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "gbps":
        return number * 1000
    if unit == "kbps":
        return number / 1000
    return number


def run_speed_test():
    """
    Measure download/upload with Apple's built-in networkQuality.
    Ping and packet loss are deliberately measured separately with ICMP.
    Falls back to speedtest-cli if networkQuality is unavailable.
    """
    ping = run_ping_test()

    try:
        nq = subprocess.run(
            ["/usr/bin/networkQuality", "-v"],
            capture_output=True, text=True, timeout=90
        )
        output = (nq.stdout or "") + "\n" + (nq.stderr or "")

        download = None
        upload = None

        for line in output.splitlines():
            if re.search(r"download capacity", line, re.I):
                download = _parse_capacity(line)
            elif re.search(r"upload capacity", line, re.I):
                upload = _parse_capacity(line)

        if download is not None or upload is not None:
            return {
                "ping": ping["ping"],
                "loss": ping["loss"],
                "jitter": ping.get("jitter"),
                "download": download,
                "upload": upload,
                "server": "Apple networkQuality",
                "raw": output,
            }
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    # Portable fallback for Macs where networkQuality isn't available.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "speedtest", "--simple"],
            capture_output=True, text=True, timeout=90
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        download = upload = None

        for line in output.splitlines():
            m = re.search(r"Download:\s*([\d.]+)\s*Mbit", line, re.I)
            if m:
                download = float(m.group(1))
            m = re.search(r"Upload:\s*([\d.]+)\s*Mbit", line, re.I)
            if m:
                upload = float(m.group(1))

        if download is not None or upload is not None:
            return {
                "ping": ping["ping"],
                "loss": ping["loss"],
                "jitter": ping.get("jitter"),
                "download": download,
                "upload": upload,
                "server": "Speedtest",
                "raw": output,
            }

        return {
            "ping": ping["ping"],
            "loss": ping["loss"],
            "jitter": ping.get("jitter"),
            "download": None,
            "upload": None,
            "server": "Unavailable",
            "raw": "A compatible speed-test service is not available on this Mac.",
        }
    except Exception as exc:
        return {
            "ping": ping["ping"],
            "loss": ping["loss"],
            "jitter": ping.get("jitter"),
            "download": None,
            "upload": None,
            "server": "Unavailable",
            "raw": str(exc),
        }

def passive_sniff(count=40):
    """Capture packet headers on this Mac's active Wi-Fi interface only."""
    interface=get_wifi_interface()
    try:
        result=subprocess.run(
            ["/usr/sbin/tcpdump","-i",interface,"-nn","-c",str(count),"-q"],
            capture_output=True,text=True,timeout=30)
        output=result.stdout.strip()
        if result.returncode != 0 and not output:
            return f"Packet monitor error: {result.stderr.strip()}"
        return output or "No packets captured."
    except subprocess.TimeoutExpired:
        return "Packet monitor timed out."
    except Exception as exc:
        return f"Packet monitor unavailable: {exc}"

def _metric_quality_ping(ms):
    if ms is None:
        return "Unavailable", MUTED
    if ms < 30:
        return "Excellent", GREEN
    if ms < 60:
        return "Very good", GREEN
    if ms < 100:
        return "Good", YELLOW
    if ms < 180:
        return "Fair", YELLOW
    return "High latency", RED


def _metric_quality_speed(mbps):
    if mbps is None:
        return "Unavailable", MUTED
    if mbps >= 100:
        return "Excellent", GREEN
    if mbps >= 50:
        return "Very good", GREEN
    if mbps >= 25:
        return "Good", YELLOW
    if mbps >= 10:
        return "Fair", YELLOW
    return "Slow", RED


def _metric_quality_loss(loss):
    if loss is None:
        return "Unavailable", MUTED
    if loss <= 0:
        return "Excellent", GREEN
    if loss < 1:
        return "Very good", GREEN
    if loss < 3:
        return "Acceptable", YELLOW
    if loss < 10:
        return "High loss", RED
    return "Severe loss", RED


def _packet_loss_explanation(loss):
    if loss is None:
        return (
            "Packet loss could not be measured yet. Run the speed test again "
            "while connected to Wi-Fi."
        )
    if loss <= 0:
        return (
            "No packets were lost during the latency test. That means every "
            "test packet reached its destination and a reply came back."
        )
    if loss < 1:
        return (
            f"{loss:.1f}% of test packets were lost. This is very low and "
            "usually has little noticeable effect."
        )
    if loss < 3:
        return (
            f"{loss:.1f}% of test packets were lost. You may notice occasional "
            "lag or brief interruptions during real-time traffic."
        )
    return (
        f"{loss:.1f}% of test packets were lost. This can cause lag, buffering, "
        "dropped calls, and slow or unreliable connections."
    )


def _format_speed(value):
    return "--" if value is None else f"{value:.2f} Mbps"


def calculate_connection_score(result):
    """Estimate overall connection quality from measured test results."""
    download = result.get("download")
    upload = result.get("upload")
    ping = result.get("ping")
    loss = result.get("loss")
    jitter = result.get("jitter")

    score = 0.0
    score += min(100.0, (download / 100.0) * 100.0) * 0.30 if download is not None else 0
    score += min(100.0, (upload / 50.0) * 100.0) * 0.15 if upload is not None else 0
    score += max(0.0, min(100.0, 100.0 - max(0.0, ping - 20.0))) * 0.30 if ping is not None else 0
    score += max(0.0, 100.0 - loss * 20.0) * 0.15 if loss is not None else 0
    score += max(0.0, 100.0 - jitter * 2.0) * 0.10 if jitter is not None else 0
    return int(max(0, min(100, round(score))))


def score_description(score):
    if score >= 90:
        return "Excellent connection", GREEN
    if score >= 75:
        return "Very good connection", GREEN
    if score >= 55:
        return "Good connection", YELLOW
    if score >= 35:
        return "Fair connection", YELLOW
    return "Poor connection", RED


def open_tools():
    win = tk.Toplevel(root)
    win.title("Network Tools")
    screen_w = win.winfo_screenwidth()
    screen_h = win.winfo_screenheight()
    w = min(1260, screen_w - 70)
    h = min(860, screen_h - 70)
    win.geometry(f"{w}x{h}")
    win.minsize(1000, 720)
    win.configure(bg=BG)

    # ---------- Scrollable Network Tools page ----------
    # All Network Tools sections live inside this canvas so the user can
    # reach Test History and the Packet Monitor on smaller screens.
    outer = tk.Frame(win, bg=BG)
    outer.pack(fill="both", expand=True)

    canvas = tk.Canvas(
        outer, bg=BG, highlightthickness=0, bd=0
    )
    page_scrollbar = ttk.Scrollbar(
        outer, orient="vertical", command=canvas.yview
    )
    canvas.configure(yscrollcommand=page_scrollbar.set)

    page_scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    content = tk.Frame(canvas, bg=BG)
    content_window = canvas.create_window(
        (0, 0), window=content, anchor="nw"
    )

    def update_scroll_region(_event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def resize_content(event):
        canvas.itemconfigure(content_window, width=event.width)

    content.bind("<Configure>", update_scroll_region)
    canvas.bind("<Configure>", resize_content)

    def scroll_page(event):
        if event.delta:
            canvas.yview_scroll(
                -max(1, int(abs(event.delta) / 30)) *
                (1 if event.delta > 0 else -1),
                "units"
            )

    # Bind scrolling at the toplevel so the page scrolls even when the
    # pointer is over a label, button, tree, or empty area.
    def scroll_page(event):
        delta = getattr(event, "delta", 0)
        if delta:
            canvas.yview_scroll(
                -max(1, int(abs(delta) / 30)) * (1 if delta > 0 else -1),
                "units"
            )
        return "break"

    scroll_bind_id = f"network_tools_scroll_{id(win)}"
    root.bind_class(scroll_bind_id, "<MouseWheel>", scroll_page)
    for widget in (canvas, content):
        widget.bindtags((scroll_bind_id,) + widget.bindtags())

    def close_tools():
        try:
            root.unbind_class(scroll_bind_id, "<MouseWheel>")
        except Exception:
            pass
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", close_tools)

    # ---------- Header ----------
    top = tk.Frame(content, bg=BG)
    top.pack(fill="x", padx=26, pady=(18, 6))

    tk.Frame(top, bg=ORANGE, width=5, height=52).pack(
        side="left", padx=(0, 14)
    )

    tk.Label(
        top, text="◉", font=("Helvetica", 30, "bold"),
        fg=CYAN, bg=BG
    ).pack(side="left", padx=(0, 14))

    title_box = tk.Frame(top, bg=BG)
    title_box.pack(side="left")

    tk.Label(
        title_box, text="NETWORK TOOLS",
        font=("Helvetica", 24, "bold"), fg=WHITE, bg=BG
    ).pack(anchor="w")

    tk.Label(
        title_box,
        text="Run network tests and monitor your connection in real time.",
        font=("Helvetica", 10), fg=MUTED, bg=BG
    ).pack(anchor="w", pady=(2, 0))

    overall_status = tk.Label(
        top, text="● READY",
        font=("Helvetica", 10, "bold"),
        fg=GREEN, bg=BG
    )
    overall_status.pack(side="right", pady=(10, 0))

    # ---------- Speed test ----------
    speed_card = card(content)
    speed_card.pack(fill="x", padx=20, pady=(8, 10))

    speed_header = tk.Frame(speed_card, bg=PANEL)
    speed_header.pack(fill="x", padx=20, pady=(15, 0))

    tk.Label(
        speed_header, text="◌  INTERNET SPEED TEST",
        font=("Helvetica", 13, "bold"), fg=ORANGE_BRIGHT, bg=PANEL
    ).pack(side="left")

    tk.Label(
        speed_header,
        text="Real download, upload, latency and packet-loss measurements.",
        font=("Helvetica", 9), fg=MUTED, bg=PANEL
    ).pack(side="left", padx=16)

    # Primary action sits ABOVE the results so the test control is visible
    # before the user sees the current/previous measurements.
    speed_action_row = tk.Frame(speed_card, bg=PANEL)
    speed_action_row.pack(fill="x", padx=20, pady=(10, 2))

    test_button = tk.Button(
        speed_action_row,
        text="⚡  RUN SPEED TEST",
        command=lambda: None,
        font=("Helvetica", 10, "bold"),
        fg="#120803",
        bg=ORANGE,
        activebackground=ORANGE_BRIGHT,
        activeforeground="#120803",
        relief="flat",
        bd=0,
        padx=18,
        pady=9,
        cursor="hand2"
    )
    test_button.pack(side="left")

    tk.Label(
        speed_action_row,
        text="Download • Upload • Ping • Packet Loss",
        font=("Helvetica", 8, "bold"),
        fg=MUTED,
        bg=PANEL
    ).pack(side="left", padx=14)

    # Four equal metric cards — this is the stable v2-style layout.
    stats = tk.Frame(speed_card, bg=PANEL)
    stats.pack(fill="x", padx=20, pady=14)

    def make_stat(parent, title, value, accent=CYAN):
        f = tk.Frame(
            parent, bg="#0c151e",
            highlightbackground=BORDER, highlightthickness=1
        )
        f.pack(side="left", fill="both", expand=True, padx=5)

        tk.Label(
            f, text=title,
            font=("Helvetica", 9, "bold"),
            fg=accent, bg="#0c151e"
        ).pack(anchor="w", padx=15, pady=(13, 2))

        val = tk.Label(
            f, text=value,
            font=("Helvetica", 22, "bold"),
            fg=WHITE, bg="#0c151e"
        )
        val.pack(anchor="w", padx=15)

        quality = tk.Label(
            f, text="Waiting for test",
            font=("Helvetica", 9, "bold"),
            fg=MUTED, bg="#0c151e"
        )
        quality.pack(anchor="w", padx=15, pady=(2, 12))

        return val, quality

    download_value, download_quality = make_stat(stats, "↓  DOWNLOAD", "-- Mbps", CYAN)
    upload_value, upload_quality = make_stat(stats, "↑  UPLOAD", "-- Mbps", "#9b6cff")
    ping_value, ping_quality = make_stat(stats, "◉  PING", "-- ms", "#b26cff")
    loss_value, loss_quality = make_stat(stats, "%  PACKET LOSS", "--", "#f5c451")

    # These variables must exist before the health widgets are created.
    # v29 created them later in open_tools(), which caused Network Tools to
    # stop building immediately after the four metric cards.
    health_text_var = tk.StringVar(value="Health: —")
    health_score_var = tk.StringVar(value="—")

    # Details row.
    details = tk.Frame(speed_card, bg="#0c151e",
                       highlightbackground=BORDER, highlightthickness=1)
    details.pack(fill="x", padx=20, pady=(0, 12))

    server_var = tk.StringVar(value="Server: —")
    host_var = tk.StringVar(value=f"Latency host: {PING_HOST}")
    jitter_var = tk.StringVar(value="Jitter: —")
    duration_var = tk.StringVar(value="Test time: —")
    time_var = tk.StringVar(value="Last test: —")

    for var in (server_var, host_var, jitter_var, duration_var, time_var):
        tk.Label(
            details, textvariable=var,
            font=("Helvetica", 8), fg=MUTED, bg="#0c151e"
        ).pack(side="left", padx=12, pady=9)

    health_box = tk.Frame(details, bg="#0c151e")
    health_box.pack(side="right", padx=12, pady=4)

    health_text_label = tk.Label(
        health_box, textvariable=health_text_var,
        font=("Helvetica", 8, "bold"),
        fg=CYAN, bg="#0c151e"
    )
    health_text_label.pack(anchor="e")

    health_score_label = tk.Label(
        health_box, textvariable=health_score_var,
        font=("Helvetica", 14, "bold"),
        fg=WHITE, bg="#0c151e"
    )
    health_score_label.pack(anchor="e")

    # Explanation + action row.
    explanation = tk.Frame(
        speed_card, bg="#0c151e",
        highlightbackground=BORDER, highlightthickness=1
    )
    explanation.pack(fill="x", padx=20, pady=(0, 15))

    # Right-side action column has a fixed width so its controls can never
    # be squeezed by the explanatory text.
    action_buttons = tk.Frame(
        explanation, bg="#0c151e", width=205
    )
    action_buttons.pack(
        side="right", fill="y", padx=(12, 10), pady=8
    )
    action_buttons.pack_propagate(False)

    def make_action_button(text, command, accent=False):
        if accent:
            btn = tk.Button(
                action_buttons,
                text=text,
                command=command,
                font=("Helvetica", 9, "bold"),
                fg="#061016",
                bg=CYAN,
                activebackground="#5cecff",
                activeforeground="#061016",
                disabledforeground="#64737d",
                relief="flat", bd=0,
                padx=10, pady=8,
                cursor="hand2"
            )
        else:
            btn = make_flat_button(
                action_buttons, text, command,
                bg_color="#263544", fg_color=WHITE,
                hover_bg="#3b4e61", hover_fg=WHITE,
                padx=10, pady=8
            )
        btn.pack(fill="x", padx=2, pady=3)
        return btn

    copy_button = make_action_button("COPY RESULTS", lambda: None)
    export_button = make_action_button("EXPORT HISTORY", lambda: None)
    # The speed-test action is now at the TOP of the speed-test card.
    # Keep COPY RESULTS and EXPORT HISTORY in the lower action column.

    # Left side: explanation text gets whatever width remains.
    explanation_body = tk.Frame(explanation, bg="#0c151e")
    explanation_body.pack(
        side="left", fill="both", expand=True,
        padx=(14, 8), pady=10
    )

    explanation_icon = tk.Label(
        explanation_body, text="✓",
        font=("Helvetica", 22, "bold"),
        fg=GREEN, bg="#0c151e"
    )
    explanation_icon.pack(side="left", padx=(0, 9))

    explanation_text = tk.Frame(explanation_body, bg="#0c151e")
    explanation_text.pack(side="left", fill="both", expand=True)

    connection_label = tk.Label(
        explanation_text,
        text="Ready to test your connection.",
        font=("Helvetica", 10, "bold"),
        fg=WHITE, bg="#0c151e",
        anchor="w"
    )
    connection_label.pack(anchor="w", fill="x")

    loss_explain_label = tk.Label(
        explanation_text,
        text=(
            "Packet loss is the percentage of test packets that failed to "
            "receive a reply. 0% means every test packet came back successfully."
        ),
        font=("Helvetica", 8),
        fg=MUTED, bg="#0c151e",
        justify="left",
        anchor="w",
        wraplength=620
    )
    loss_explain_label.pack(anchor="w", fill="x", pady=(2, 0))

    # ---------- New network diagnostics ----------
    diagnostics_card = card(content)
    diagnostics_card.pack(fill="x", padx=20, pady=(0, 10))

    diagnostics_header = tk.Frame(diagnostics_card, bg=PANEL)
    diagnostics_header.pack(fill="x", padx=20, pady=(10, 4))

    tk.Label(
        diagnostics_header,
        text="NETWORK DIAGNOSTICS",
        font=("Helvetica", 10, "bold"),
        fg=CYAN, bg=PANEL
    ).pack(side="left")

    tk.Label(
        diagnostics_header,
        text="Stability, gaming quality and plain-English diagnosis",
        font=("Helvetica", 8), fg=MUTED, bg=PANEL
    ).pack(side="left", padx=12)

    # Stability
    stability_row = tk.Frame(
        diagnostics_card, bg="#0c151e",
        highlightbackground=BORDER, highlightthickness=1
    )
    stability_row.pack(fill="x", padx=20, pady=4)

    stability_info = tk.Frame(stability_row, bg="#0c151e")
    stability_info.pack(side="left", fill="x", expand=True)

    tk.Label(
        stability_info, text="LATENCY STABILITY",
        font=("Helvetica", 9, "bold"),
        fg="#9b6cff", bg="#0c151e"
    ).pack(anchor="w", padx=12, pady=(8, 1))

    stability_value = tk.Label(
        stability_info, text="Not tested",
        font=("Helvetica", 11, "bold"),
        fg=WHITE, bg="#0c151e"
    )
    stability_value.pack(anchor="w", padx=12)

    tk.Label(
        stability_info,
        text="30-second test • average/min/max ping • jitter • packet loss",
        font=("Helvetica", 7), fg=MUTED, bg="#0c151e"
    ).pack(anchor="w", padx=12, pady=(1, 8))

    stability_run_button = make_flat_button(
        stability_row, "RUN STABILITY TEST", lambda: None,
        bg_color="#263544", fg_color=WHITE,
        hover_bg="#3b4e61", hover_fg=WHITE,
        padx=12, pady=7
    )
    stability_run_button.pack(side="right", padx=12)

    # Gaming
    gaming_row = tk.Frame(
        diagnostics_card, bg="#0c151e",
        highlightbackground=BORDER, highlightthickness=1
    )
    gaming_row.pack(fill="x", padx=20, pady=4)

    gaming_info = tk.Frame(gaming_row, bg="#0c151e")
    gaming_info.pack(side="left", fill="x", expand=True)

    tk.Label(
        gaming_info, text="GAMING QUALITY",
        font=("Helvetica", 9, "bold"),
        fg=YELLOW, bg="#0c151e"
    ).pack(anchor="w", padx=12, pady=(8, 1))

    gaming_value = tk.Label(
        gaming_info, text="Run the stability test",
        font=("Helvetica", 11, "bold"),
        fg=WHITE, bg="#0c151e"
    )
    gaming_value.pack(anchor="w", padx=12)

    tk.Label(
        gaming_info,
        text="Uses latency, jitter and packet loss — not download speed",
        font=("Helvetica", 7), fg=MUTED, bg="#0c151e"
    ).pack(anchor="w", padx=12, pady=(1, 8))

    gaming_run_button = make_flat_button(
        gaming_row, "TEST GAMING QUALITY", lambda: None,
        bg_color="#263544", fg_color=WHITE,
        hover_bg="#3b4e61", hover_fg=WHITE,
        padx=12, pady=7
    )
    gaming_run_button.pack(side="right", padx=12)

    # Diagnosis
    diagnosis_row = tk.Frame(
        diagnostics_card, bg="#0c151e",
        highlightbackground=BORDER, highlightthickness=1
    )
    diagnosis_row.pack(fill="x", padx=20, pady=(4, 10))

    diagnosis_value = tk.Label(
        diagnosis_row,
        text="Run a speed test to get an automatic connection diagnosis.",
        font=("Helvetica", 9, "bold"),
        fg=WHITE, bg="#0c151e",
        justify="left", wraplength=700
    )
    diagnosis_value.pack(side="left", fill="x", expand=True, padx=12, pady=10)

    diagnosis_copy_button = make_flat_button(
        diagnosis_row, "COPY DIAGNOSIS", lambda: None,
        bg_color="#263544", fg_color=WHITE,
        hover_bg="#3b4e61", hover_fg=WHITE,
        padx=12, pady=7
    )
    diagnosis_copy_button.pack(side="right", padx=12)

    stability_running = {"value": False}

    def gaming_rating(avg, jitter, loss):
        if avg is None or loss is None:
            return "Unable to rate"
        if loss == 0 and avg <= 30 and (jitter is None or jitter <= 5):
            return "Excellent"
        if loss <= 1 and avg <= 50 and (jitter is None or jitter <= 10):
            return "Good"
        if loss <= 3 and avg <= 80:
            return "Fair"
        return "Poor"

    def run_stability_test():
        if stability_running["value"]:
            return

        stability_running["value"] = True
        values = []

        stability_run_button.set_enabled(False)
        gaming_run_button.set_enabled(False)
        stability_run_button.label.config(text="TESTING…")
        gaming_run_button.label.config(text="TESTING…")
        stability_value.config(text="Measuring latency…")
        gaming_value.config(text="Measuring latency…")

        def worker():
            for _ in range(30):
                try:
                    result = subprocess.run(
                        ["/sbin/ping", "-c", "1", "-W", "1000", PING_HOST],
                        capture_output=True, text=True, timeout=3
                    )
                    match = re.search(r"time[=<]([\d.]+)\s*ms", result.stdout)
                    values.append(float(match.group(1)) if match else None)
                except Exception:
                    values.append(None)
                time.sleep(1)

            valid = [v for v in values if v is not None]
            lost = len(values) - len(valid)
            avg = sum(valid) / len(valid) if valid else None
            minimum = min(valid) if valid else None
            maximum = max(valid) if valid else None
            jitter = None
            if len(valid) > 1:
                diffs = [abs(valid[i] - valid[i-1]) for i in range(1, len(valid))]
                jitter = sum(diffs) / len(diffs)
            loss = (lost / len(values) * 100) if values else None
            rating = gaming_rating(avg, jitter, loss)

            def finish():
                stability_running["value"] = False
                stability_run_button.set_enabled(True)
                gaming_run_button.set_enabled(True)
                stability_run_button.label.config(text="RUN STABILITY TEST")
                gaming_run_button.label.config(text="TEST GAMING QUALITY")

                if avg is None:
                    stability_value.config(text="No replies received")
                else:
                    stability_value.config(
                        text=(
                            f"Avg {avg:.1f} ms  •  Min {minimum:.1f} ms  •  "
                            f"Max {maximum:.1f} ms"
                        )
                    )

                gaming_value.config(
                    text=(
                        f"{rating}  •  {avg:.1f} ms ping  •  "
                        f"{jitter:.1f} ms jitter  •  {loss:.1f}% packet loss"
                    )
                    if avg is not None and jitter is not None and loss is not None
                    else rating
                )

            root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    stability_run_button.set_command(run_stability_test)
    gaming_run_button.set_command(run_stability_test)

    def automatic_diagnosis(result):
        ping = result.get("ping")
        loss = result.get("loss")
        download = result.get("download")

        try:
            wifi = get_wifi_info()
            rssi = wifi.get("rssi") if isinstance(wifi, dict) else None
        except Exception:
            rssi = None

        if loss is not None and loss >= 3:
            return (
                "Packet loss is high. Some data is failing to reach its "
                "destination, which can cause lag, buffering and dropped calls."
            )
        if isinstance(rssi, (int, float)) and rssi <= -70:
            return (
                f"Your Wi-Fi signal is weak ({rssi} dBm). Move closer to the "
                "router or try a less congested band."
            )
        if ping is not None and ping >= 80:
            return (
                f"Your latency is high ({ping:.0f} ms). Internet speed may be "
                "fine, but games and real-time calls can feel delayed."
            )
        if download is not None and download < 25:
            return (
                f"Download speed is relatively low ({download:.1f} Mbps). "
                "Check other devices using the connection and test again."
            )
        if loss is not None and loss == 0:
            return "Your connection looks healthy: no packet loss was detected."
        return "Your connection looks generally healthy. Run the stability test to check for intermittent spikes."

    def copy_diagnosis():
        win.clipboard_clear()
        win.clipboard_append(diagnosis_value.cget("text"))
        win.update()
        connection_label.config(text="Diagnosis copied to clipboard.", fg=GREEN)

    diagnosis_copy_button.set_command(copy_diagnosis)

    # ---------- Test history ----------
    history_card = card(content)
    history_card.pack(fill="x", padx=20, pady=(0, 10))

    history_header = tk.Frame(history_card, bg=PANEL)
    history_header.pack(fill="x", padx=20, pady=(8, 2))

    tk.Label(
        history_header, text="TEST HISTORY",
        font=("Helvetica", 10, "bold"),
        fg=CYAN, bg=PANEL
    ).pack(side="left")

    tk.Label(
        history_header,
        text="Last 5 tests from this session",
        font=("Helvetica", 8), fg=MUTED, bg=PANEL
    ).pack(side="left", padx=12)

    history_tree = ttk.Treeview(
        history_card,
        columns=("time", "download", "upload", "ping", "loss", "score"),
        show="headings",
        height=3,
        style="Modern.Treeview"
    )

    history_headers = {
        "time": "TIME", "download": "DOWNLOAD", "upload": "UPLOAD",
        "ping": "PING", "loss": "LOSS", "score": "HEALTH"
    }

    for col in history_headers:
        history_tree.heading(col, text=history_headers[col])
        history_tree.column(
            col,
            width=145 if col == "time" else 105,
            anchor="center"
        )

    history_tree.pack(fill="x", padx=20, pady=(2, 9))

    # ---------- Packet monitor ----------
    monitor = card(content)
    monitor.pack(fill="x", padx=20, pady=(0, 20))

    monitor_header = tk.Frame(monitor, bg=PANEL)
    monitor_header.pack(fill="x", padx=20, pady=(13, 5))

    title_area = tk.Frame(monitor_header, bg=PANEL)
    title_area.pack(side="left")

    tk.Label(
        title_area, text="♢  PASSIVE PACKET MONITOR",
        font=("Helvetica", 13, "bold"), fg=CYAN, bg=PANEL
    ).pack(anchor="w")

    tk.Label(
        title_area,
        text="Shows packet metadata observed on this Mac's active Wi-Fi interface.",
        font=("Helvetica", 8), fg=MUTED, bg=PANEL
    ).pack(anchor="w", pady=(2, 0))

    monitor_status = tk.Label(
        monitor_header, text="● READY",
        font=("Helvetica", 9, "bold"),
        fg=GREEN, bg=PANEL
    )
    monitor_status.pack(side="right", padx=(10, 0))

    clear_button = make_flat_button(
        monitor_header,
        "CLEAR",
        lambda: None,
        bg_color="#263544",
        fg_color=WHITE,
        hover_bg="#3b4e61",
        hover_fg=WHITE,
        padx=11,
        pady=6
    )
    clear_button.pack(side="right")

    monitor_button = tk.Button(
        monitor_header, text="START MONITORING",
        font=("Helvetica", 8, "bold"),
        fg="#061016", bg="#00d9f5",
        activebackground="#5cecff", activeforeground="#061016",
        disabledforeground="#64737d",
        relief="flat", bd=0, padx=12, pady=6,
        cursor="hand2"
    )
    monitor_button.pack(side="right", padx=(7, 0))

    summary = tk.Frame(monitor, bg=PANEL)
    summary.pack(fill="x", padx=20, pady=6)

    total_var = tk.StringVar(value="0")
    rate_var = tk.StringVar(value="0 pkt/s")
    protocol_var = tk.StringVar(value="—")
    connections_var = tk.StringVar(value="0")

    def summary_box(title, variable, subtitle):
        f = tk.Frame(
            summary, bg="#0c151e",
            highlightbackground=BORDER, highlightthickness=1
        )
        f.pack(side="left", fill="both", expand=True, padx=4)

        tk.Label(
            f, text=title, font=("Helvetica", 8, "bold"),
            fg=MUTED, bg="#0c151e"
        ).pack(anchor="w", padx=12, pady=(8, 0))

        tk.Label(
            f, textvariable=variable,
            font=("Helvetica", 17, "bold"),
            fg=WHITE, bg="#0c151e"
        ).pack(anchor="w", padx=12)

        tk.Label(
            f, text=subtitle, font=("Helvetica", 7),
            fg=MUTED, bg="#0c151e"
        ).pack(anchor="w", padx=12, pady=(0, 8))

    summary_box("TOTAL PACKETS", total_var, "Captured")
    summary_box("AVERAGE RATE", rate_var, "Packets per second")
    summary_box("MOST COMMON", protocol_var, "Protocol")
    summary_box("ACTIVE CONNECTIONS", connections_var, "Unique endpoints")

    # Packet table with a compact vertical scrollbar.
    table_frame = tk.Frame(monitor, bg=PANEL)
    table_frame.pack(fill="both", expand=True, padx=20, pady=7)

    columns = ("time", "source", "destination", "protocol", "length")
    tree = ttk.Treeview(
        table_frame, columns=columns, show="headings",
        style="Modern.Treeview"
    )

    headings = {
        "time": "TIME",
        "source": "SOURCE",
        "destination": "DESTINATION",
        "protocol": "PROTOCOL",
        "length": "SIZE"
    }

    widths = {
        "time": 95,
        "source": 205,
        "destination": 205,
        "protocol": 95,
        "length": 90
    }

    for col in columns:
        tree.heading(col, text=headings[col])
        tree.column(col, width=widths[col], anchor="w")

    tree.pack(fill="both", expand=True, side="left")

    scrollbar = ttk.Scrollbar(
        table_frame, orient="vertical", command=tree.yview
    )
    scrollbar.pack(side="right", fill="y")
    tree.configure(yscrollcommand=scrollbar.set)

    # Plain-English packet loss panel.
    info = tk.Frame(
        monitor, bg="#0c151e",
        highlightbackground=BORDER, highlightthickness=1
    )
    info.pack(fill="x", padx=20, pady=(0, 14))

    tk.Label(
        info, text="ⓘ", font=("Helvetica", 20, "bold"),
        fg=BLUE, bg="#0c151e"
    ).pack(side="left", padx=(15, 9), pady=10)

    info_text = tk.Frame(info, bg="#0c151e")
    info_text.pack(side="left", fill="x", expand=True, pady=8)

    tk.Label(
        info_text, text="WHAT IS PACKET LOSS?",
        font=("Helvetica", 9, "bold"),
        fg=CYAN, bg="#0c151e"
    ).pack(anchor="w")

    packet_help = tk.Label(
        info_text,
        text=(
            "Packet loss means some data never reached its destination. "
            "High loss can cause lag, buffering, voice-call drops and retries. "
            "For a normal connection, 0% is ideal."
        ),
        font=("Helvetica", 8), fg=MUTED, bg="#0c151e",
        justify="left", wraplength=760
    )
    packet_help.pack(anchor="w", pady=(2, 0))

    packet_status = tk.Label(
        info, text="● No packet-loss result yet",
        font=("Helvetica", 8, "bold"),
        fg=MUTED, bg="#0c151e"
    )
    packet_status.pack(side="right", padx=16)

    # ---------- Shared state ----------
    running = {"speed": False}
    sniff_running = {"value": False}
    packet_count = {"value": 0}
    recent_times = deque(maxlen=30)
    protocols = defaultdict(int)
    endpoints = set()

    def set_quality(label, text_value, color):
        label.config(text=text_value, fg=color)

    def result_text(result):
        ping = result.get("ping")
        loss = result.get("loss")
        download = result.get("download")
        upload = result.get("upload")
        jitter = result.get("jitter")
        return (
            "Wi-Fi Visualizer Network Test\n"
            f"Download: {_format_speed(download)}\n"
            f"Upload: {_format_speed(upload)}\n"
            f"Ping: {ping:.1f} ms\n" if ping is not None else
            "Wi-Fi Visualizer Network Test\n"
            f"Download: {_format_speed(download)}\n"
            f"Upload: {_format_speed(upload)}\n"
            "Ping: unavailable\n"
        ) + (
            f"Jitter: {jitter:.1f} ms\n" if jitter is not None else "Jitter: unavailable\n"
        ) + (
            f"Packet loss: {loss:.1f}%\n" if loss is not None else "Packet loss: unavailable\n"
        ) + f"Server: {result.get('server', 'Unavailable')}"

    last_result = {"value": None}
    test_history = []

    def copy_results():
        if not last_result["value"]:
            connection_label.config(text="Run a speed test first.", fg=YELLOW)
            return
        win.clipboard_clear()
        win.clipboard_append(result_text(last_result["value"]))
        win.update()
        connection_label.config(text="Test results copied to clipboard.", fg=GREEN)

    copy_button.set_command(copy_results)

    def refresh_history():
        for item in history_tree.get_children():
            history_tree.delete(item)

        for item in reversed(test_history[-5:]):
            history_tree.insert(
                "",
                "end",
                values=(
                    item["timestamp"],
                    _format_speed(item.get("download")),
                    _format_speed(item.get("upload")),
                    f'{item["ping"]:.1f} ms' if item.get("ping") is not None else "—",
                    f'{item["loss"]:.1f}%' if item.get("loss") is not None else "—",
                    f'{item["score"]}/100'
                )
            )

    def export_history():
        if not test_history:
            connection_label.config(
                text="Run a speed test before exporting history.",
                fg=YELLOW
            )
            return

        path = filedialog.asksaveasfilename(
            parent=win,
            title="Export Wi-Fi Test History",
            defaultextension=".csv",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "Time", "Download Mbps", "Upload Mbps",
                    "Ping ms", "Jitter ms", "Packet Loss %",
                    "Health Score", "Server"
                ])
                for item in test_history:
                    writer.writerow([
                        item["timestamp"],
                        item.get("download"),
                        item.get("upload"),
                        item.get("ping"),
                        item.get("jitter"),
                        item.get("loss"),
                        item.get("score"),
                        item.get("server")
                    ])

            connection_label.config(
                text="Test history exported successfully.",
                fg=GREEN
            )
        except Exception as exc:
            connection_label.config(
                text=f"Could not export history: {str(exc)[:65]}",
                fg=RED
            )

    export_button.set_command(export_history)

    def speed_worker():
        if running["speed"]:
            return

        running["speed"] = True
        started_at = time.time()

        def ui_start():
            test_button.config(state="disabled", text="TESTING…")
            copy_button.set_enabled(False)
            overall_status.config(text="● TESTING", fg=YELLOW)
            connection_label.config(
                text="Testing your internet connection…", fg=WHITE
            )
            loss_explain_label.config(
                text=(
                    "Measuring real latency, download speed, upload speed "
                    "and packet loss. This may take a little while."
                )
            )
            monitor_status.config(text="● SPEED TEST RUNNING", fg=YELLOW)

        root.after(0, ui_start)

        result = run_speed_test()
        result["duration"] = time.time() - started_at
        result["timestamp"] = time.strftime("%d %b %Y, %I:%M:%S %p")
        last_result["value"] = result

        def ui_done():
            running["speed"] = False
            test_button.config(state="normal", text="↻  RUN SPEED TEST")
            copy_button.set_enabled(True)

            ping = result.get("ping")
            loss = result.get("loss")
            download = result.get("download")
            upload = result.get("upload")
            jitter = result.get("jitter")

            download_value.config(text=_format_speed(download))
            upload_value.config(text=_format_speed(upload))
            ping_value.config(text=f"{ping:.1f} ms" if ping is not None else "--")
            loss_value.config(
                text=f"{loss:.1f}%" if loss is not None else "--"
            )

            pq, pc = _metric_quality_ping(ping)
            uq, uc = _metric_quality_speed(upload)
            dq, dc = _metric_quality_speed(download)
            lq, lc = _metric_quality_loss(loss)

            set_quality(ping_quality, pq, pc)
            set_quality(upload_quality, uq, uc)
            set_quality(download_quality, dq, dc)
            set_quality(loss_quality, lq, lc)

            score = calculate_connection_score(result)
            score_text, score_color = score_description(score)
            result["score"] = score
            test_history.append(result)

            health_text_var.set(f"Health: {score_text}")
            health_score_var.set(f"{score}/100")
            health_text_label.config(fg=score_color)
            health_score_label.config(fg=score_color)
            refresh_history()
            diagnosis_value.config(text=automatic_diagnosis(result))

            server_var.set(f"Server: {result.get('server', 'Unavailable')}")
            host_var.set(f"Latency host: {PING_HOST}")
            jitter_var.set(
                f"Jitter: {jitter:.1f} ms" if jitter is not None else "Jitter: —"
            )
            duration_var.set(f"Test time: {result['duration']:.1f}s")
            time_var.set(f"Last test: {result['timestamp']}")

            explanation = _packet_loss_explanation(loss)
            loss_explain_label.config(text=explanation)

            if loss is not None and loss <= 0 and ping is not None and ping < 60:
                connection_label.config(
                    text="Your internet connection is excellent.",
                    fg=GREEN
                )
                explanation_icon.config(text="✓", fg=GREEN)
                packet_status.config(
                    text="● No packet loss detected", fg=GREEN
                )
            elif loss is not None and loss < 3:
                connection_label.config(
                    text="Your connection is working, with minor packet loss.",
                    fg=YELLOW
                )
                explanation_icon.config(text="!", fg=YELLOW)
                packet_status.config(
                    text=f"● {loss:.1f}% packet loss detected", fg=YELLOW
                )
            elif loss is not None:
                connection_label.config(
                    text="Your connection is experiencing packet loss.",
                    fg=RED
                )
                explanation_icon.config(text="!", fg=RED)
                packet_status.config(
                    text=f"● {loss:.1f}% packet loss detected", fg=RED
                )
            else:
                connection_label.config(
                    text="Speed test completed; packet loss could not be measured.",
                    fg=YELLOW
                )
                explanation_icon.config(text="!", fg=YELLOW)

            overall_status.config(text="● READY", fg=GREEN)
            monitor_status.config(
                text="● MONITORING" if sniff_running["value"] else "● READY",
                fg=CYAN if sniff_running["value"] else GREEN
            )

        root.after(0, ui_done)

    test_button.config(
        command=lambda: threading.Thread(
            target=speed_worker, daemon=True
        ).start()
    )

    # ---------- Packet monitor ----------
    def parse_tcpdump(line):
        m = re.match(
            r"^(\\d+:\\d+:\\d+\\.\\d+)\\s+IP6?\\s+(\\S+)\\s+>\\s+(\\S+):\\s+(.+)$",
            line.strip()
        )
        if not m:
            return None

        timestamp, source, destination, details = m.groups()
        protocol = details.split()[0].upper() if details else "OTHER"
        length_match = re.search(r"\\b(\\d+)\\s*$", details)
        length = f"{length_match.group(1)} bytes" if length_match else "—"

        endpoints.add((source, destination))
        protocols[protocol] += 1
        recent_times.append(time.time())

        return (timestamp, source, destination, protocol, length)

    def sniff_worker():
        if sniff_running["value"]:
            return

        sniff_running["value"] = True
        interface = get_wifi_interface()

        def started():
            monitor_button.config(
                text="■  STOP MONITORING",
                fg=WHITE,
                bg="#9b3038",
                activebackground="#c44750",
                activeforeground=WHITE
            )
            monitor_status.config(text="● MONITORING", fg=CYAN)

        root.after(0, started)

        process = None

        try:
            process = subprocess.Popen(
                [
                    "/usr/sbin/tcpdump", "-i", interface,
                    "-nn", "-l", "-q"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            started_at = time.time()

            for raw in iter(process.stdout.readline, ""):
                if (
                    not sniff_running["value"]
                    or time.time() - started_at > 300
                ):
                    break

                row = parse_tcpdump(raw)
                if not row:
                    continue

                packet_count["value"] += 1
                count = packet_count["value"]

                def add_row(r=row, c=count):
                    tree.insert("", "end", values=r)

                    items = tree.get_children()
                    if len(items) > 100:
                        tree.delete(items[0])

                    total_var.set(str(c))

                    if recent_times:
                        elapsed = max(1, time.time() - recent_times[0])
                        rate_var.set(
                            f"{len(recent_times) / elapsed:.1f} pkt/s"
                        )

                    if protocols:
                        protocol_var.set(
                            max(protocols, key=protocols.get).upper()
                        )

                    connections_var.set(str(len(endpoints)))

                root.after(0, add_row)

        except Exception as exc:
            root.after(0, lambda: monitor_status.config(
                text=f"● MONITOR ERROR: {str(exc)[:45]}",
                fg=RED
            ))
        finally:
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

            sniff_running["value"] = False

            def stopped():
                monitor_button.config(
                    text="START MONITORING",
                    fg="#061016",
                    bg="#00d9f5",
                    activebackground="#5cecff",
                    activeforeground="#061016"
                )
                if not running["speed"]:
                    monitor_status.config(text="● READY", fg=GREEN)

            root.after(0, stopped)

    def start_monitor():
        if sniff_running["value"]:
            sniff_running["value"] = False
            monitor_status.config(text="● STOPPING…", fg=YELLOW)
            return

        threading.Thread(target=sniff_worker, daemon=True).start()

    monitor_button.config(command=start_monitor)

    def clear_packets():
        for item in tree.get_children():
            tree.delete(item)

        packet_count["value"] = 0
        recent_times.clear()
        protocols.clear()
        endpoints.clear()

        total_var.set("0")
        rate_var.set("0 pkt/s")
        protocol_var.set("—")
        connections_var.set("0")

    # Attach the real clear action after clear_packets() is defined.
    clear_button.set_command(clear_packets)



    # ---------- Connection Insights ----------
    # Lightweight diagnostics that do not interfere with the existing speed
    # test, packet monitor, or stability test.
    insights_card = card(content)
    insights_card.pack(fill="x", padx=20, pady=(4, 10))

    insights_header = tk.Frame(insights_card, bg=PANEL)
    insights_header.pack(fill="x", padx=20, pady=(14, 4))

    tk.Label(
        insights_header,
        text="◈  CONNECTION INSIGHTS",
        font=("Helvetica", 13, "bold"),
        fg=ORANGE_BRIGHT,
        bg=PANEL
    ).pack(side="left")

    tk.Label(
        insights_header,
        text="Quick checks for your local network and DNS connection.",
        font=("Helvetica", 9),
        fg=MUTED,
        bg=PANEL
    ).pack(side="left", padx=16)

    insights_grid = tk.Frame(insights_card, bg=PANEL)
    insights_grid.pack(fill="x", padx=20, pady=(8, 14))

    def insight_box(parent, title, value="—", subtitle="Not checked", accent=ORANGE):
        box = tk.Frame(
            parent,
            bg="#0c151e",
            highlightbackground=BORDER,
            highlightthickness=1
        )
        box.pack(side="left", fill="both", expand=True, padx=4)

        tk.Label(
            box, text=title,
            font=("Helvetica", 8, "bold"),
            fg=accent, bg="#0c151e"
        ).pack(anchor="w", padx=12, pady=(10, 2))

        value_label = tk.Label(
            box, text=value,
            font=("Helvetica", 13, "bold"),
            fg=WHITE, bg="#0c151e"
        )
        value_label.pack(anchor="w", padx=12)

        subtitle_label = tk.Label(
            box, text=subtitle,
            font=("Helvetica", 8),
            fg=MUTED, bg="#0c151e"
        )
        subtitle_label.pack(anchor="w", padx=12, pady=(2, 10))

        return value_label, subtitle_label

    interface_value, interface_sub = insight_box(
        insights_grid, "WI-FI INTERFACE", "—", "Interface not detected", CYAN
    )
    gateway_value, gateway_sub = insight_box(
        insights_grid, "DEFAULT GATEWAY", "—", "Gateway not checked", ORANGE_BRIGHT
    )
    dns_value, dns_sub = insight_box(
        insights_grid, "DNS LATENCY", "—", "DNS not checked", "#b26cff"
    )
    local_ip_value, local_ip_sub = insight_box(
        insights_grid, "LOCAL IP", "—", "Address not detected", GREEN
    )

    insights_actions = tk.Frame(insights_card, bg=PANEL)
    insights_actions.pack(fill="x", padx=20, pady=(0, 14))

    insights_status = tk.Label(
        insights_actions,
        text="Ready for a quick network check.",
        font=("Helvetica", 8, "bold"),
        fg=MUTED,
        bg=PANEL
    )
    insights_status.pack(side="left", padx=(4, 12))

    def _run_command(args, timeout=4):
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout
            )
            return result.stdout.strip(), result.returncode
        except Exception:
            return "", 1

    def _get_default_interface():
        out, rc = _run_command(["/sbin/route", "-n", "get", "default"])
        if rc != 0:
            return None
        match = re.search(r"\ninterface:\s*(\S+)", "\n" + out)
        return match.group(1) if match else None

    def _get_local_ip(interface):
        if not interface:
            return None
        out, rc = _run_command(
            ["/usr/sbin/ipconfig", "getifaddr", interface]
        )
        return out if rc == 0 and out else None

    def _get_gateway():
        out, rc = _run_command(["/sbin/route", "-n", "get", "default"])
        if rc != 0:
            return None
        match = re.search(r"\ngateway:\s*([0-9.]+)", "\n" + out)
        return match.group(1) if match else None

    def _ping_host(host, count=3):
        out, rc = _run_command(
            ["/sbin/ping", "-c", str(count), "-W", "1000", host],
            timeout=8
        )
        if rc != 0:
            return None
        match = re.search(r"round-trip min/avg/max/stddev\s*=\s*"
                          r"[\d.]+/([\d.]+)/", out)
        if match:
            return float(match.group(1))
        return None

    insights_running = {"value": False}

    def run_connection_insights():
        if insights_running["value"]:
            return

        insights_running["value"] = True
        insights_refresh.set_enabled(False)
        insights_refresh.label.config(text="CHECKING…")
        insights_status.config(text="Checking interface, gateway and DNS…",
                               fg=YELLOW)

        def worker():
            interface = _get_default_interface()
            local_ip = _get_local_ip(interface)
            gateway = _get_gateway()
            dns_latency = _ping_host("1.1.1.1", 3)

            def finish():
                insights_running["value"] = False
                insights_refresh.set_enabled(True)
                insights_refresh.label.config(text="REFRESH NETWORK INFO")

                interface_value.config(text=interface or "Unavailable")
                interface_sub.config(
                    text="Active Wi-Fi interface" if interface else "No active interface"
                )

                gateway_value.config(text=gateway or "Unavailable")
                gateway_sub.config(
                    text="Default router" if gateway else "Gateway not detected"
                )

                if dns_latency is None:
                    dns_value.config(text="Unavailable")
                    dns_sub.config(text="1.1.1.1 did not reply")
                else:
                    dns_value.config(text=f"{dns_latency:.1f} ms")
                    dns_sub.config(
                        text="Excellent" if dns_latency <= 30 else
                             "Good" if dns_latency <= 60 else
                             "High latency"
                    )

                local_ip_value.config(text=local_ip or "Unavailable")
                local_ip_sub.config(
                    text="Private address on this Mac"
                    if local_ip else "Address not detected"
                )

                insights_status.config(
                    text="Network information refreshed.",
                    fg=GREEN
                )

            root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    insights_refresh = make_flat_button(
        insights_actions,
        "REFRESH NETWORK INFO",
        lambda: None,
        bg_color="#1b0e07",
        fg_color=WHITE,
        hover_bg="#321708",
        hover_fg=ORANGE_BRIGHT,
        padx=12,
        pady=7
    )
    insights_refresh.pack(side="right", padx=4)
    insights_refresh.set_command(run_connection_insights)

    # ---------- DNS Health ----------
    dns_card = card(content)
    dns_card.pack(fill="x", padx=20, pady=(0, 12))

    dns_row = tk.Frame(dns_card, bg=PANEL)
    dns_row.pack(fill="x", padx=20, pady=12)

    tk.Label(
        dns_row,
        text="DNS HEALTH",
        font=("Helvetica", 9, "bold"),
        fg="#b26cff",
        bg=PANEL
    ).pack(side="left")

    dns_health_text = tk.Label(
        dns_row,
        text="Check DNS response time and availability.",
        font=("Helvetica", 9),
        fg=MUTED,
        bg=PANEL
    )
    dns_health_text.pack(side="left", padx=16)

    dns_check_running = {"value": False}

    def run_dns_health():
        if dns_check_running["value"]:
            return

        dns_check_running["value"] = True
        dns_check_button.set_enabled(False)
        dns_check_button.label.config(text="CHECKING…")
        dns_health_text.config(text="Testing Cloudflare and Google DNS…",
                               fg=YELLOW)

        def worker():
            results = []
            for host, name in [("1.1.1.1", "Cloudflare"), ("8.8.8.8", "Google")]:
                latency = _ping_host(host, 2)
                results.append((name, latency))

            def finish():
                dns_check_running["value"] = False
                dns_check_button.set_enabled(True)
                dns_check_button.label.config(text="TEST DNS")

                available = [(name, ms) for name, ms in results if ms is not None]
                if not available:
                    dns_health_text.config(
                        text="Neither DNS server replied. Your DNS/internet connection may be unavailable.",
                        fg=RED
                    )
                    return

                best_name, best_ms = min(available, key=lambda item: item[1])
                summary = "  •  ".join(
                    f"{name}: {ms:.1f} ms" for name, ms in available
                )
                dns_health_text.config(
                    text=f"{summary}  •  Fastest: {best_name}  •  {best_ms:.1f} ms",
                    fg=GREEN if best_ms <= 60 else YELLOW
                )

            root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    dns_check_button = make_flat_button(
        dns_row,
        "TEST DNS",
        lambda: None,
        bg_color="#1b0e07",
        fg_color=WHITE,
        hover_bg="#321708",
        hover_fg=ORANGE_BRIGHT,
        padx=12,
        pady=7
    )
    dns_check_button.pack(side="right")
    dns_check_button.set_command(run_dns_health)


    # ---------- Advanced Network Lab ----------
    # Extra diagnostics designed to be useful/interesting without requiring
    # third-party packages or privileged packet capture.
    lab_card = card(content)
    lab_card.pack(fill="x", padx=20, pady=(0, 12))

    lab_header = tk.Frame(lab_card, bg=PANEL)
    lab_header.pack(fill="x", padx=20, pady=(14, 5))

    tk.Label(
        lab_header,
        text="◉  ADVANCED NETWORK LAB",
        font=("Helvetica", 13, "bold"),
        fg=ORANGE_BRIGHT,
        bg=PANEL
    ).pack(side="left")

    tk.Label(
        lab_header,
        text="Deeper checks for routing, MTU, public IP and connectivity.",
        font=("Helvetica", 9),
        fg=MUTED,
        bg=PANEL
    ).pack(side="left", padx=16)

    lab_grid = tk.Frame(lab_card, bg=PANEL)
    lab_grid.pack(fill="x", padx=20, pady=(8, 6))

    def lab_box(parent, title, value="—", subtitle="Not checked", accent=ORANGE):
        box = tk.Frame(
            parent, bg="#0c151e",
            highlightbackground=BORDER, highlightthickness=1
        )
        box.pack(side="left", fill="both", expand=True, padx=4)

        tk.Label(
            box, text=title,
            font=("Helvetica", 8, "bold"),
            fg=accent, bg="#0c151e"
        ).pack(anchor="w", padx=12, pady=(10, 2))

        value_label = tk.Label(
            box, text=value,
            font=("Helvetica", 13, "bold"),
            fg=WHITE, bg="#0c151e"
        )
        value_label.pack(anchor="w", padx=12)

        sub_label = tk.Label(
            box, text=subtitle,
            font=("Helvetica", 8),
            fg=MUTED, bg="#0c151e"
        )
        sub_label.pack(anchor="w", padx=12, pady=(2, 10))
        return value_label, sub_label

    route_value, route_sub = lab_box(
        lab_grid, "ROUTE HOPS", "—", "Trace not run", BLUE
    )
    mtu_value, mtu_sub = lab_box(
        lab_grid, "PATH MTU", "—", "MTU not checked", ORANGE_BRIGHT
    )
    public_value, public_sub = lab_box(
        lab_grid, "PUBLIC IP", "—", "Internet identity", PURPLE
    )
    captive_value, captive_sub = lab_box(
        lab_grid, "CAPTIVE PORTAL", "—", "Portal not checked", GREEN
    )

    lab_status = tk.Label(
        lab_card,
        text="These tests may briefly use the internet.",
        font=("Helvetica", 8, "bold"),
        fg=MUTED,
        bg=PANEL
    )
    lab_status.pack(side="left", padx=(24, 8), pady=(2, 14))

    lab_actions = tk.Frame(lab_card, bg=PANEL)
    lab_actions.pack(side="right", padx=20, pady=(0, 12))

    lab_running = {"value": False}

    def _traceroute_hops():
        out, rc = _run_command(
            ["/usr/sbin/traceroute", "-m", "8", "-w", "1", "1.1.1.1"],
            timeout=15
        )
        if rc != 0 and not out:
            return None
        hops = []
        for line in out.splitlines():
            match = re.match(r"\s*(\d+)\s+", line)
            if match:
                hops.append(int(match.group(1)))
        return len(hops) if hops else None

    def _path_mtu():
        # macOS ping with DF (-D) lets us find the largest payload that
        # doesn't require fragmentation. Start near the normal Ethernet MTU.
        for payload in range(1472, 1199, -8):
            out, rc = _run_command(
                ["/sbin/ping", "-D", "-c", "1", "-W", "1000",
                 "-s", str(payload), "1.1.1.1"],
                timeout=3
            )
            if rc == 0 and ("1 packets transmitted" in out or
                            "1 packets transmitted" in out):
                return payload + 28
        return None

    def _public_ip():
        out, rc = _run_command(
            ["/usr/bin/curl", "-4", "-sS", "--max-time", "5",
             "https://api.ipify.org"],
            timeout=7
        )
        value = out.strip()
        if rc == 0 and re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", value):
            return value
        return None

    def _captive_portal():
        # Apple’s captive portal endpoint is intentionally used here because
        # it is lightweight and does not require downloading a web page.
        out, rc = _run_command(
            ["/usr/bin/curl", "-L", "-sS", "--max-time", "5",
             "-o", "/dev/null", "-w", "%{http_code}",
             "http://captive.apple.com/hotspot-detect.html"],
            timeout=7
        )
        if rc != 0:
            return None
        return out.strip()

    def run_network_lab():
        if lab_running["value"]:
            return

        lab_running["value"] = True
        lab_refresh.set_enabled(False)
        lab_refresh.label.config(text="RUNNING…")
        lab_status.config(
            text="Running route, MTU, public IP and captive-portal checks…",
            fg=YELLOW
        )

        def worker():
            hops = _traceroute_hops()
            mtu = _path_mtu()
            public_ip = _public_ip()
            portal_code = _captive_portal()

            def finish():
                lab_running["value"] = False
                lab_refresh.set_enabled(True)
                lab_refresh.label.config(text="RUN NETWORK LAB")

                route_value.config(text=str(hops) if hops else "Unavailable")
                route_sub.config(
                    text="Hops to Cloudflare" if hops else "Traceroute unavailable"
                )

                mtu_value.config(
                    text=f"{mtu} bytes" if mtu else "Unavailable"
                )
                mtu_sub.config(
                    text="Estimated path MTU" if mtu else "MTU test failed"
                )

                public_value.config(text=public_ip or "Unavailable")
                public_sub.config(
                    text="IPv4 public address" if public_ip else "Could not determine"
                )

                if portal_code is None:
                    captive_value.config(text="Unknown")
                    captive_sub.config(text="Could not check")
                elif portal_code == "204":
                    captive_value.config(text="No portal")
                    captive_sub.config(text="Direct internet access")
                else:
                    captive_value.config(text="Possible portal")
                    captive_sub.config(
                        text=f"HTTP response {portal_code}"
                    )

                good = sum(x is not None for x in
                           (hops, mtu, public_ip, portal_code))
                lab_status.config(
                    text=f"Advanced checks complete • {good}/4 checks returned data.",
                    fg=GREEN if good >= 3 else YELLOW
                )

            root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    lab_refresh = make_flat_button(
        lab_actions,
        "RUN NETWORK LAB",
        lambda: None,
        bg_color="#1b0e07",
        fg_color=WHITE,
        hover_bg="#321708",
        hover_fg=ORANGE_BRIGHT,
        padx=13,
        pady=7
    )
    lab_refresh.pack()
    lab_refresh.set_command(run_network_lab)

    # ---------- Live Quality Timeline ----------
    timeline_card = card(content)
    timeline_card.pack(fill="x", padx=20, pady=(0, 12))

    timeline_header = tk.Frame(timeline_card, bg=PANEL)
    timeline_header.pack(fill="x", padx=20, pady=(14, 4))

    tk.Label(
        timeline_header,
        text="⌁  LIVE QUALITY TIMELINE",
        font=("Helvetica", 13, "bold"),
        fg=ORANGE_BRIGHT,
        bg=PANEL
    ).pack(side="left")

    timeline_value = tk.Label(
        timeline_header,
        text="No samples yet",
        font=("Helvetica", 9, "bold"),
        fg=MUTED,
        bg=PANEL
    )
    timeline_value.pack(side="right")

    timeline_canvas = tk.Canvas(
        timeline_card,
        height=110,
        bg="#080b0e",
        bd=0,
        highlightbackground=BORDER,
        highlightthickness=1
    )
    timeline_canvas.pack(fill="x", padx=20, pady=(6, 14))

    timeline_running = {"value": False}

    def draw_quality_timeline():
        timeline_canvas.delete("all")
        w = max(timeline_canvas.winfo_width(), 600)
        h = 110
        samples = list(recent_times)[-30:] if recent_times else []

        # Grid.
        for y in (25, 55, 85):
            timeline_canvas.create_line(
                0, y, w, y, fill="#1d2227"
            )

        if len(samples) < 2:
            timeline_canvas.create_text(
                w / 2, h / 2,
                text="Run a stability test to populate the quality timeline",
                fill=MUTED,
                font=("Helvetica", 9)
            )
            return

        maximum = max(samples) if max(samples) > 0 else 1
        points = []
        for idx, value in enumerate(samples):
            x = 10 + idx * (w - 20) / max(len(samples) - 1, 1)
            y = 95 - min(value / maximum, 1.0) * 75
            points.extend((x, y))

        timeline_canvas.create_line(
            *points,
            fill=ORANGE_BRIGHT,
            width=2,
            smooth=True
        )

        for idx in range(0, len(points), 2):
            x, y = points[idx], points[idx + 1]
            timeline_canvas.create_oval(
                x - 2, y - 2, x + 2, y + 2,
                fill=ORANGE,
                outline=""
            )

        timeline_value.config(
            text=f"Last {len(samples)} latency samples • "
                 f"{samples[-1]:.1f} ms latest"
        )

    timeline_canvas.bind("<Configure>", lambda e: draw_quality_timeline())

    # Existing stability samples are stored in `recent_times`; refresh the
    # visual when the Network Tools window is active without creating another
    # network worker.
    def timeline_tick():
        if not win.winfo_exists():
            return
        draw_quality_timeline()
        win.after(1000, timeline_tick)

    win.after(300, timeline_tick)

    canvas.update_idletasks()
    canvas.configure(scrollregion=canvas.bbox("all"))


# ============================================================
# MODERN UI
# ============================================================

# Black -> orange visual theme.
# Tkinter does not support native gradients on Frame widgets, so the theme
# uses a controlled progression of near-black, charcoal, burnt-orange and
# bright orange accents to create the same gradient feel without changing
# the application's widget structure or behavior.
BG = "#050505"
PANEL = "#0c0907"
PANEL_2 = "#120c09"
BORDER = "#5a2a0b"
ORANGE = "#ff6a00"
ORANGE_BRIGHT = "#ff8f24"
ORANGE_SOFT = "#c84f0a"
WHITE = "#f7f4ef"
MUTED = "#9b9086"
GREEN = "#3be38a"
YELLOW = "#ffc857"
RED = "#ff6472"
BLUE = "#55a9ff"
PURPLE = "#a875ff"

# Keep CYAN as an alias so existing feature code continues to work, but
# point the dashboard's primary accent at orange.
CYAN = ORANGE

def card(parent, **kwargs):
    return tk.Frame(parent, bg=PANEL, highlightbackground=BORDER,
                    highlightthickness=1, bd=0, **kwargs)


def make_flat_button(parent, text, command, *,
                     bg_color="#263544", fg_color=WHITE,
                     hover_bg="#3b4e61", hover_fg=WHITE,
                     padx=14, pady=7):
    # Frame + Label avoids the macOS Aqua renderer overriding tk.Button
    # foreground/background colors on some Tk builds.
    frame = tk.Frame(
        parent,
        bg=bg_color,
        highlightbackground=bg_color,
        highlightcolor=bg_color,
        highlightthickness=1,
        cursor="hand2"
    )

    label = tk.Label(
        frame,
        text=text,
        font=("Helvetica", 9, "bold"),
        fg=fg_color,
        bg=bg_color,
        padx=padx,
        pady=pady,
        cursor="hand2"
    )
    # Let the frame size itself from the label's text/padding.
    # Disabling geometry propagation without an explicit width/height makes
    # custom buttons collapse to ~1px-high lines on macOS Tk.
    label.pack(fill="both", expand=True)

    def enter(_event):
        if not frame._enabled:
            return
        frame.config(bg=hover_bg, highlightbackground=hover_bg)
        label.config(bg=hover_bg, fg=hover_fg)

    def leave(_event):
        if not frame._enabled:
            return
        frame.config(bg=bg_color, highlightbackground=bg_color)
        label.config(bg=bg_color, fg=fg_color)

    def click(_event):
        if frame._enabled and callable(command):
            command()
        return "break"

    for widget in (frame, label):
        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)
        widget.bind("<Button-1>", click)

    frame.label = label
    frame._enabled = True

    def set_command(new_command):
        nonlocal command
        command = new_command

    def set_enabled(enabled):
        frame._enabled = bool(enabled)
        if enabled:
            label.config(fg=fg_color, bg=bg_color)
            frame.config(
                bg=bg_color,
                highlightbackground=bg_color,
                cursor="hand2"
            )
            label.config(cursor="hand2")
        else:
            label.config(fg="#687681", bg="#1b242d")
            frame.config(
                bg="#1b242d",
                highlightbackground="#1b242d",
                cursor="arrow"
            )
            label.config(cursor="arrow")

    frame.set_command = set_command
    frame.set_enabled = set_enabled
    return frame

def make_label(parent, text="", size=10, bold=False, fg=WHITE, bg=PANEL, **kwargs):
    return tk.Label(parent, text=text,
                    font=("Helvetica", size, "bold" if bold else "normal"),
                    fg=fg, bg=bg, **kwargs)

# ============================================================
# MAIN WINDOW
# ============================================================

root = tk.Tk()
root.title("Wi-Fi Visualizer")

# Screen-aware sizing so the complete dashboard remains visible.
screen_w = root.winfo_screenwidth()
screen_h = root.winfo_screenheight()
window_w = min(1320, screen_w - 80)
window_h = min(760, screen_h - 100)
window_w = max(window_w, 1000)
window_h = max(window_h, 650)

root.geometry(f"{window_w}x{window_h}")
root.minsize(1000, 650)
root.configure(bg=BG)

root.update_idletasks()
x = max(0, (screen_w - window_w) // 2)
y = max(0, (screen_h - window_h) // 2)
root.geometry(f"{window_w}x{window_h}+{x}+{y}")

# ============================================================
# BLACK -> ORANGE GRADIENT BACKGROUND
# ============================================================
# Created before the dashboard widgets and permanently lowered behind
# them. This changes the background only; all existing features and
# button commands remain untouched.

def _blend_hex(a, b, t):
    a = a.lstrip("#")
    b = b.lstrip("#")
    ar = tuple(int(a[i:i+2], 16) for i in (0, 2, 4))
    br = tuple(int(b[i:i+2], 16) for i in (0, 2, 4))
    return "#{:02x}{:02x}{:02x}".format(
        *(round(ar[i] + (br[i] - ar[i]) * t) for i in range(3))
    )

gradient_bg = tk.Canvas(
    root, bg="#050505", bd=0, highlightthickness=0
)
gradient_bg.place(x=0, y=0, relwidth=1, relheight=1)
root.tk.call("lower", gradient_bg._w)

def redraw_gradient():
    gradient_bg.delete("all")
    w = max(root.winfo_width(), 1000)
    h = max(root.winfo_height(), 650)

    # Broad black -> dark burnt-orange transition, strongest on the right.
    steps = max(90, w // 8)
    for i in range(steps):
        t = i / max(steps - 1, 1)
        orange_t = max(0.0, (t - 0.55) / 0.45) ** 1.7
        color = _blend_hex("#050505", "#451605", orange_t)
        x0 = int(i * w / steps)
        x1 = int((i + 1) * w / steps) + 1
        gradient_bg.create_rectangle(
            x0, 0, x1, h, fill=color, outline=""
        )

    # Upper-right orange glow, matching the supplied black/orange reference.
    cx = int(w * 0.94)
    cy = int(h * 0.18)
    max_r = int(max(w, h) * 0.62)

    for r in range(max_r, 20, -16):
        strength = (1.0 - r / max_r) ** 2
        color = _blend_hex(
            "#180702", "#ff5200",
            0.025 + strength * 0.27
        )
        gradient_bg.create_oval(
            cx - r * 1.20, cy - r * 0.58,
            cx + r * 1.20, cy + r * 0.58,
            fill=color, outline=""
        )

    # Lower-right ember glow.
    cx2 = int(w * 0.84)
    cy2 = int(h * 0.90)
    max_r2 = int(max(w, h) * 0.48)

    for r in range(max_r2, 20, -16):
        strength = (1.0 - r / max_r2) ** 2
        color = _blend_hex(
            "#120501", "#ff4b00",
            0.02 + strength * 0.18
        )
        gradient_bg.create_oval(
            cx2 - r * 1.25, cy2 - r * 0.42,
            cx2 + r * 1.25, cy2 + r * 0.42,
            fill=color, outline=""
        )

    # Critical: keep the background below every existing dashboard widget.
    root.tk.call("lower", gradient_bg._w)

redraw_gradient()

def _resize_gradient(event):
    if event.widget is root:
        pending = getattr(root, "_gradient_after", None)
        if pending:
            root.after_cancel(pending)
        root._gradient_after = root.after(80, redraw_gradient)

root.bind("<Configure>", _resize_gradient, add="+")

style = ttk.Style()
try:
    style.theme_use("clam")
except Exception:
    pass
style.configure("Modern.Treeview", background=PANEL_2, foreground=WHITE,
                fieldbackground=PANEL_2, borderwidth=0, rowheight=32,
                font=("Helvetica", 10))
style.configure("Modern.Treeview.Heading", background="#182531",
                foreground=CYAN, borderwidth=0, relief="flat",
                font=("Helvetica", 9, "bold"))
style.map("Modern.Treeview", background=[("selected", "#164758")],
          foreground=[("selected", WHITE)])

# Header
header = tk.Frame(root, bg=BG)
header.pack(fill="x", padx=28, pady=(22, 8))
titlebox = tk.Frame(header, bg=BG)
titlebox.pack(side="left")
tk.Label(titlebox, text="Wi-Fi Visualizer", font=("Helvetica", 30, "bold"),
         fg=WHITE, bg=BG).pack(anchor="w")
tk.Label(titlebox, text="REAL-TIME WIRELESS ANALYTICS  •  LIVE CONNECTION MONITOR",
         font=("Helvetica", 9, "bold"), fg=ORANGE_BRIGHT, bg=BG).pack(anchor="w", pady=(3,0))
header_accent = tk.Frame(root, bg=ORANGE, height=3)
header_accent.pack(fill="x", padx=28, pady=(0, 12))

def open_network_settings():
    """Open macOS Network settings using the current and legacy preference URLs."""
    urls = (
        "x-apple.systempreferences:com.apple.Network-Settings.extension",
        "x-apple.systempreferences:com.apple.preference.network",
    )
    for url in urls:
        try:
            result = subprocess.run(
                ["/usr/bin/open", url],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return
        except Exception:
            pass

def launch_scanner():
    open_scanner()

# Header actions are kept in their own container. All three controls
# are actual children of the same action row, so none can be displaced.
header_actions = tk.Frame(header, bg=BG)
header_actions.pack(side="right", padx=(18, 0), pady=2)

status_pill = tk.Frame(
    header_actions, bg="#12241d",
    highlightbackground="#214b36", highlightthickness=1
)
status_pill.pack(side="top", anchor="e", pady=(0, 6))

status = tk.Label(
    status_pill,
    text="  ●  CONNECTING  ",
    font=("Helvetica", 9, "bold"),
    fg=YELLOW,
    bg="#12241d"
)
status.pack(padx=2, pady=3)

action_row = tk.Frame(header_actions, bg=BG)
action_row.pack(side="top", anchor="e")

network_settings_button = make_flat_button(
    action_row,
    "NETWORK SETTINGS",
    open_network_settings,
    bg_color="#263544",
    fg_color=WHITE,
    hover_bg="#3b4e61",
    hover_fg=CYAN,
    padx=14,
    pady=7
)
network_settings_button.pack(side="left", padx=(0, 8), pady=2)

tools_button = make_flat_button(
    action_row,
    "NETWORK TOOLS",
    open_tools,
    bg_color="#263544",
    fg_color=WHITE,
    hover_bg="#3b4e61",
    hover_fg=CYAN,
    padx=14,
    pady=7
)
tools_button.pack(side="left", padx=8, pady=2)

scan_button = tk.Button(
    action_row,
    text="SCAN WI-FI",
    command=launch_scanner,
    font=("Helvetica", 9, "bold"),
    fg="#120803",
    bg=ORANGE,
    activebackground=ORANGE_BRIGHT,
    activeforeground="#120803",
    relief="flat",
    bd=0,
    padx=16,
    pady=8,
    cursor="hand2",
)
scan_button.pack(side="left", padx=(8, 0), pady=2)

# Metrics row
metrics = tk.Frame(root, bg=BG)
metrics.pack(fill="x", padx=28, pady=(2, 14))

# Two-tone accent: dark burnt orange fading into bright orange.
metric_glow = tk.Frame(root, bg="#2a160c", height=1)
metric_glow.pack(fill="x", padx=28, pady=(0, 2))

def metric_card(title, subtitle):
    f = card(metrics)
    f.pack(side="left", fill="both", expand=True, padx=5)

    accent = {
        "Signal": "#8f3f0f",
        "SNR": ORANGE_SOFT,
        "Channel": ORANGE,
        "Health": ORANGE_BRIGHT
    }.get(title, ORANGE)

    tk.Frame(f, bg=accent, height=2).pack(fill="x")
    make_label(f, title.upper(), 8, True, MUTED).pack(
        anchor="w", padx=16, pady=(12, 2)
    )
    v = make_label(f, "--", 22, True, WHITE)
    v.pack(anchor="w", padx=16)
    make_label(f, subtitle, 8, False, MUTED).pack(
        anchor="w", padx=16, pady=(1, 12)
    )
    return v


signal_metric = metric_card("Signal", "dBm")
snr_metric = metric_card("SNR", "signal-to-noise")
channel_metric = metric_card("Channel", "current channel")
health_metric = metric_card("Health", "connection score")

# Main content
content = tk.Frame(root, bg=BG)
content.pack(fill="both", expand=True, padx=28, pady=(0, 16))

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

wifi_cache = {"data": None, "busy": False}

def wifi_worker():
    if wifi_cache["busy"]:
        return
    wifi_cache["busy"] = True
    try:
        wifi_cache["data"] = get_wifi_info()
    finally:
        wifi_cache["busy"] = False

def request_wifi_update():
    if not wifi_cache["busy"]:
        threading.Thread(target=wifi_worker, daemon=True).start()

def update_dashboard():
    data = wifi_cache["data"]
    if not data:
        # The worker may still be querying macOS. Do not leave the UI looking
        # frozen; keep retrying and show a clear state.
        status.config(text="●  CONNECTING TO WI-FI…", fg=YELLOW)
        root.after(500, update_dashboard)
        return
    rssi_match = re.search(r"-\d+", str(data.get("rssi", "")))
    noise_match = re.search(r"-\d+", str(data.get("noise", "")))

    # Never fake a -100 dBm reading. If parsing fails, keep the last real value.
    if rssi_match:
        rssi = int(rssi_match.group(0))
    elif signal_history:
        rssi = signal_history[-1]
    else:
        status.config(text="●  WI-FI NOT DETECTED", fg=YELLOW)
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

    # Keep BOTH loops alive:
    # 1. request_wifi_update() refreshes the macOS Wi-Fi data.
    # 2. update_dashboard() consumes that data and redraws the history graph.
    # Previously only the worker was scheduled here, so the graph stopped
    # after the first successful reading.
    root.after(UPDATE_MS, request_wifi_update)
    root.after(UPDATE_MS, update_dashboard)

request_wifi_update()
update_dashboard()
root.mainloop()
