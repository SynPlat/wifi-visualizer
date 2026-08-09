import subprocess
import re


def scan_wifi():

    result = subprocess.run(
        ["system_profiler", "SPAirPortDataType"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print("❌ Unable to scan Wi-Fi networks.")
        return []

    lines = result.stdout.splitlines()

    networks = []
    current = None

    for raw_line in lines:

        line = raw_line.strip()

        # ----------------------------------------------------
        # Ignore empty lines
        # ----------------------------------------------------

        if not line:
            continue


        # ----------------------------------------------------
        # Detect a new SSID
        #
        # SSID lines appear before PHY Mode / Channel etc.
        # ----------------------------------------------------

        if (
            not line.startswith("PHY Mode:")
            and not line.startswith("Channel:")
            and not line.startswith("Network Type:")
            and not line.startswith("Security:")
            and not line.startswith("Signal / Noise:")
            and not line.startswith("Transmit Rate:")
            and not line.startswith("Country Code:")
            and not line.startswith("Wireless Diagnostics:")
            and ":" not in line
        ):

            if current is not None:
                networks.append(current)

            current = {
                "ssid": line,
                "channel": "Unknown",
                "band": "Unknown",
                "signal": None,
                "noise": None,
                "phy": "Unknown",
                "security": "Unknown",
                "width": "Unknown"
            }

            continue


        # ----------------------------------------------------
        # If we haven't found an SSID yet, skip
        # ----------------------------------------------------

        if current is None:
            continue


        # ----------------------------------------------------
        # PHY MODE
        # ----------------------------------------------------

        if line.startswith("PHY Mode:"):

            current["phy"] = line.split(
                ":",
                1
            )[1].strip()


        # ----------------------------------------------------
        # CHANNEL
        # ----------------------------------------------------

        elif line.startswith("Channel:"):

            channel_text = line.split(
                ":",
                1
            )[1].strip()

            current["channel"] = channel_text

            # Extract channel number
            channel_match = re.search(
                r"\b(\d+)\b",
                channel_text
            )

            if channel_match:

                channel_number = int(
                    channel_match.group(1)
                )

                if "2GHz" in channel_text:

                    current["band"] = "2.4 GHz"

                elif "5GHz" in channel_text:

                    current["band"] = "5 GHz"

                elif "6GHz" in channel_text:

                    current["band"] = "6 GHz"


        # ----------------------------------------------------
        # SECURITY
        # ----------------------------------------------------

        elif line.startswith("Security:"):

            current["security"] = line.split(
                ":",
                1
            )[1].strip()


        # ----------------------------------------------------
        # SIGNAL / NOISE
        # ----------------------------------------------------

        elif line.startswith("Signal / Noise:"):

            value = line.split(
                ":",
                1
            )[1].strip()

            numbers = re.findall(
                r"-?\d+",
                value
            )

            if len(numbers) >= 1:

                current["signal"] = int(
                    numbers[0]
                )

            if len(numbers) >= 2:

                current["noise"] = int(
                    numbers[1]
                )


        # ----------------------------------------------------
        # NETWORK TYPE
        # ----------------------------------------------------

        elif line.startswith("Network Type:"):

            current["network_type"] = line.split(
                ":",
                1
            )[1].strip()


        # ----------------------------------------------------
        # TRANSMIT RATE
        # ----------------------------------------------------

        elif line.startswith("Transmit Rate:"):

            current["tx_rate"] = line.split(
                ":",
                1
            )[1].strip()


    # Add final network
    if current is not None:
        networks.append(current)


    # --------------------------------------------------------
    # Remove incomplete entries
    # --------------------------------------------------------

    networks = [
        network
        for network in networks
        if network["channel"] != "Unknown"
    ]


    return networks


# ============================================================
# CHANNEL ANALYSIS
# ============================================================

def channel_analysis(networks):

    channels = {}

    for network in networks:

        channel_text = network["channel"]

        match = re.search(
            r"\b(\d+)\b",
            channel_text
        )

        if not match:
            continue

        channel = int(
            match.group(1)
        )

        if channel not in channels:

            channels[channel] = {
                "count": 0,
                "strong": 0,
                "signals": []
            }

        channels[channel]["count"] += 1

        signal = network["signal"]

        if signal is not None:

            channels[channel]["signals"].append(
                signal
            )

            if signal >= -70:

                channels[channel]["strong"] += 1


    return channels


# ============================================================
# DISPLAY
# ============================================================

def display_networks(networks):

    print()

    print(
        "╔══════════════════════════════════════════════════════════════╗"
    )

    print(
        "║                 📡 WI-FI ENVIRONMENT                        ║"
    )

    print(
        "╠══════════════════════════════════════════════════════════════╣"
    )

    print(
        f"║ {'SSID':22} {'CH':5} {'BAND':9} {'SIGNAL':8} {'SECURITY':12} ║"
    )

    print(
        "╟──────────────────────────────────────────────────────────────╢"
    )


    for network in networks:

        ssid = network["ssid"][:22]

        channel_match = re.search(
            r"\b(\d+)\b",
            network["channel"]
        )

        channel = (
            channel_match.group(1)
            if channel_match
            else "?"
        )

        band = network["band"]

        signal = network["signal"]

        signal_text = (
            f"{signal} dBm"
            if signal is not None
            else "?"
        )

        security = network["security"][:12]


        print(
            f"║ {ssid:<22} "
            f"{channel:<5} "
            f"{band:<9} "
            f"{signal_text:<8} "
            f"{security:<12} ║"
        )


    print(
        "╚══════════════════════════════════════════════════════════════╝"
    )


# ============================================================
# DISPLAY CHANNEL ANALYSIS
# ============================================================

def display_channels(networks):

    channels = channel_analysis(
        networks
    )


    print()

    print(
        "╔══════════════════════════════════════════════════════════════╗"
    )

    print(
        "║                  📊 CHANNEL ANALYSIS                        ║"
    )

    print(
        "╠══════════════════════════════════════════════════════════════╣"
    )


    if not channels:

        print(
            "║ No channel information available.                          ║"
        )

    else:

        for channel in sorted(
            channels
        ):

            data = channels[channel]

            count = data["count"]

            strong = data["strong"]

            signals = data["signals"]


            if signals:

                strongest = max(
                    signals
                )

            else:

                strongest = None


            if count == 1:

                quality = "🟢 LOW"

            elif count == 2:

                quality = "🟡 MEDIUM"

            else:

                quality = "🔴 HIGH"


            print(
                f"║ Channel {channel:<3} "
                f"Networks: {count:<3} "
                f"Strong: {strong:<3} "
                f"{quality:<10} ║"
            )


    print(
        "╚══════════════════════════════════════════════════════════════╝"
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print()

    print(
        "🔍 Scanning nearby Wi-Fi networks..."
    )

    networks = scan_wifi()


    print(
        f"✅ Found {len(networks)} networks"
    )


    if networks:

        display_networks(
            networks
        )

        display_channels(
            networks
        )

    else:

        print(
            "❌ No networks found."
        )