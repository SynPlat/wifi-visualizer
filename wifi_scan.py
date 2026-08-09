import subprocess
import re


def scan_wifi():

    result = subprocess.run(
        ["system_profiler", "SPAirPortDataType"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print("❌ Could not scan Wi-Fi.")
        return []

    lines = result.stdout.splitlines()

    networks = []

    inside_networks = False
    current = None

    for raw in lines:

        stripped = raw.strip()

        # Start of nearby network section
        if stripped == "Other Local Wi-Fi Networks:":
            inside_networks = True
            continue

        if not inside_networks:
            continue

        # Stop if we reach another major section
        if stripped and not raw.startswith(" "):
            break

        # ----------------------------------------------------
        # NEW NETWORK
        # ----------------------------------------------------
        #
        # SSID lines are indented and end with :
        # but aren't one of the property names below.
        #

        known_fields = (
            "PHY Mode:",
            "Channel:",
            "Network Type:",
            "Security:",
            "Signal / Noise:",
            "Transmit Rate:",
            "Country Code:"
        )

        if (
            raw.startswith("        ")
            and stripped.endswith(":")
            and not stripped.startswith(known_fields)
        ):

            if current is not None:
                networks.append(current)

            ssid = stripped[:-1].strip()

            current = {
                "ssid": ssid,
                "channel": None,
                "band": None,
                "signal": None,
                "noise": None,
                "phy": None,
                "security": None,
                "width": None
            }

            continue

        if current is None:
            continue

        # ----------------------------------------------------
        # PHY MODE
        # ----------------------------------------------------

        if stripped.startswith("PHY Mode:"):

            current["phy"] = stripped.split(
                ":", 1
            )[1].strip()

        # ----------------------------------------------------
        # CHANNEL
        # ----------------------------------------------------

        elif stripped.startswith("Channel:"):

            channel_text = stripped.split(
                ":", 1
            )[1].strip()

            current["channel"] = channel_text

            if "2GHz" in channel_text:
                current["band"] = "2.4 GHz"

            elif "5GHz" in channel_text:
                current["band"] = "5 GHz"

            elif "6GHz" in channel_text:
                current["band"] = "6 GHz"

        # ----------------------------------------------------
        # SECURITY
        # ----------------------------------------------------

        elif stripped.startswith("Security:"):

            current["security"] = stripped.split(
                ":", 1
            )[1].strip()

        # ----------------------------------------------------
        # SIGNAL / NOISE
        # ----------------------------------------------------

        elif stripped.startswith("Signal / Noise:"):

            value = stripped.split(
                ":", 1
            )[1]

            numbers = re.findall(
                r"-?\d+",
                value
            )

            if len(numbers) >= 1:
                current["signal"] = int(numbers[0])

            if len(numbers) >= 2:
                current["noise"] = int(numbers[1])

    # Add final network
    if current is not None:
        networks.append(current)

    return networks


# ============================================================
# CHANNEL ANALYSIS
# ============================================================

def analyze_channels(networks):

    channels = {}

    for network in networks:

        if not network["channel"]:
            continue

        match = re.search(
            r"\b(\d+)\b",
            network["channel"]
        )

        if not match:
            continue

        channel = int(match.group(1))

        if channel not in channels:
            channels[channel] = {
                "count": 0,
                "signals": []
            }

        channels[channel]["count"] += 1

        if network["signal"] is not None:
            channels[channel]["signals"].append(
                network["signal"]
            )

    return channels


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print()
    print("🔍 Scanning nearby Wi-Fi networks...")
    print()

    networks = scan_wifi()

    print(
        f"✅ Found {len(networks)} networks"
    )

    print()

    # --------------------------------------------------------
    # NETWORK LIST
    # --------------------------------------------------------

    print("╔════════════════════════════════════════════════════════════╗")
    print("║                 📡 WI-FI ENVIRONMENT                     ║")
    print("╠════════════════════════════════════════════════════════════╣")

    if not networks:

        print("║ No networks found.                                       ║")

    else:

        for network in networks:

            ssid = network["ssid"][:22]

            channel_match = re.search(
                r"\b(\d+)\b",
                network["channel"] or ""
            )

            channel = (
                channel_match.group(1)
                if channel_match
                else "?"
            )

            band = network["band"] or "?"

            signal = (
                f"{network['signal']} dBm"
                if network["signal"] is not None
                else "?"
            )

            print(
                f"║ {ssid:<22} "
                f"CH {channel:<3} "
                f"{band:<9} "
                f"{signal:<8} ║"
            )

    print("╚════════════════════════════════════════════════════════════╝")


    # --------------------------------------------------------
    # CHANNEL ANALYSIS
    # --------------------------------------------------------

    channels = analyze_channels(networks)

    print()
    print("╔════════════════════════════════════════════════════════════╗")
    print("║                  📊 CHANNEL ANALYSIS                     ║")
    print("╠════════════════════════════════════════════════════════════╣")

    if not channels:

        print("║ No channel information found.                            ║")

    else:

        for channel in sorted(channels):

            data = channels[channel]

            count = data["count"]

            if count == 1:
                status = "🟢 LOW"

            elif count == 2:
                status = "🟡 MEDIUM"

            else:
                status = "🔴 HIGH"

            print(
                f"║ Channel {channel:<3} "
                f"Networks: {count:<3} "
                f"{status:<10}                         ║"
            )

    print("╚════════════════════════════════════════════════════════════╝")
    print()
