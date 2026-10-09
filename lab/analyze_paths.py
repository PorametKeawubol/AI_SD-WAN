"""Read filtered underlay captures; encrypted frame counts are not UDP delivery."""

import argparse
import ipaddress
import json
import math
import statistics
import struct
from collections import Counter
from pathlib import Path

MAGIC = {
    b"\xd4\xc3\xb2\xa1": ("<", 1e6),
    b"\xa1\xb2\xc3\xd4": (">", 1e6),
    b"\x4d\x3c\xb2\xa1": ("<", 1e9),
    b"\xa1\xb2\x3c\x4d": (">", 1e9),
}


def pcap_frames(path):
    """Validate classic PCAP and Ethernet/VLAN IPv4 before exposing frame metadata."""
    with Path(path).open("rb") as stream:
        header = stream.read(24)
        if len(header) != 24 or header[:4] not in MAGIC:
            raise ValueError("Missing or unsupported PCAP header")
        order, resolution = MAGIC[header[:4]]
        major, minor, _, _, snaplen, link_type = struct.unpack(order + "HHIIII", header[4:])
        if (major, minor) != (2, 4) or link_type != 1 or not 14 <= snaplen <= 65535:
            raise ValueError("Expected classic Ethernet PCAP")
        while record := stream.read(16):
            if len(record) != 16:
                raise ValueError("Truncated PCAP record")
            seconds, fraction, captured, wire = struct.unpack(order + "IIII", record)
            if fraction >= resolution or not 14 <= captured <= min(snaplen, wire):
                raise ValueError("Invalid PCAP record length or timestamp")
            frame = stream.read(captured)
            if len(frame) != captured:
                raise ValueError("Truncated captured frame")
            offset = 14
            protocol = frame[12:14]
            for _ in range(2):
                if protocol not in (b"\x81\x00", b"\x88\xa8"):
                    break
                if len(frame) < offset + 4:
                    raise ValueError("Truncated VLAN header")
                protocol = frame[offset + 2 : offset + 4]
                offset += 4
            if protocol != b"\x08\x00":
                continue
            if len(frame) < offset + 20 or frame[offset] >> 4 != 4:
                raise ValueError("Truncated or invalid IPv4 header")
            ihl = (frame[offset] & 15) * 4
            if ihl < 20 or len(frame) < offset + ihl:
                raise ValueError("Invalid IPv4 options length")
            yield {
                "timestamp": seconds + fraction / resolution,
                "source": str(ipaddress.IPv4Address(frame[offset + 12 : offset + 16])),
                "destination": str(ipaddress.IPv4Address(frame[offset + 16 : offset + 20])),
                "ip_protocol": frame[offset + 9],
                "wire_bytes": wire,
            }


def time_origin(directory, packets):
    started = packets.get("started_at")
    if isinstance(started, (float, int)) and math.isfinite(started) and started > 0:
        return {"epoch": started, "method": "udp_generator_recorded_wall_start"}
    file = directory / "edge-observations.json"
    if file.exists():
        frames = json.loads(file.read_text(encoding="utf-8"))["frames"]
        origins = [
            row["reference_state_written_at"] - row["reference_state_second"] for row in frames
        ]
        if origins:
            origin = statistics.median(origins)
            return {
                "epoch": origin,
                "method": "estimated_from_api_state_file_write_time",
                "maximum_origin_deviation_seconds": max(abs(value - origin) for value in origins),
            }
    raise ValueError("No recorded or defensible estimated capture time origin")


def analyze_trial(directory):
    directory = Path(directory)
    packets = json.loads((directory / "packets.json").read_text(encoding="utf-8"))
    origin = time_origin(directory, packets)
    counts, sizes, foreign = {}, {}, {}
    for index in (1, 2, 3):
        link = f"wan{index}"
        counter, size_count, ignored = Counter(), Counter(), 0
        for frame in pcap_frames(directory / (link + ".pcap")):
            a, b = f"172.16.{index}.1", f"172.16.{index}.2"
            pair = (frame["source"], frame["destination"])
            if pair not in ((a, b), (b, a)) or frame["wire_bytes"] <= 1000:
                ignored += 1
                continue
            direction = "a_to_b" if pair == (a, b) else "b_to_a"
            counter[(math.floor(frame["timestamp"] - origin["epoch"]), direction)] += 1
            size_count[(frame["wire_bytes"], frame["ip_protocol"])] += 1
        counts[link], foreign[link] = counter, ignored
        sizes[link] = [
            {"wire_bytes": wire, "ip_protocol": protocol, "frames": count}
            for (wire, protocol), count in sorted(size_count.items())
        ]

    def phase(start, end):
        rows = [row for row in packets["intervals"] if start <= row["second"] < end]
        return {
            "start_second": start,
            "end_second": end,
            "sent_udp_packets": sum(row["sent_packets"] for row in rows),
            "received_udp_echoes": sum(row["received_packets"] for row in rows),
            "captured_underlay_frames": {
                link: {
                    direction: sum(
                        count
                        for (second, way), count in counter.items()
                        if start <= second < end and way == direction
                    )
                    for direction in ("a_to_b", "b_to_a")
                }
                for link, counter in counts.items()
            },
        }

    return {
        "trial": directory.name,
        "evidence_level": "real_filtered_underlay_captures",
        "time_origin": origin,
        "source_port": packets.get("source_port"),
        "destination_port": packets.get("destination_port"),
        "phases": {
            "healthy_baseline": phase(10, 70),
            "fault": phase(70, 150),
            "post_grace": phase(80, 150),
            "restoration": phase(150, 170),
        },
        "five_second_segments": [phase(second, second + 5) for second in range(65, 120, 5)],
        "wire_sizes_and_protocols": sizes,
        "foreign_frames_ignored": foreign,
        "limitations": [
            "Captures contain encrypted underlay frames; application payload/sequence cannot be read",
            "One namespace capture interface per WAN; counts are observed frames, not end-to-end delivery",
            "Both directional impairment and capture placement affect which drops are visible",
            "UDP echo receipt in packets.json is the delivery and SLA evidence",
            "Epoch PCAP timestamps and monotonic traffic bins can drift slightly; segment boundaries are approximate",
        ],
    }


def analyze_experiment(directory):
    directory = Path(directory)
    rows, excluded = [], []
    for file in sorted(directory.glob("S*/trial.json")):
        detail = json.loads(file.read_text(encoding="utf-8"))
        if detail.get("status") != "completed" or not detail.get("packet_measurement_completed"):
            excluded.append({"trial": file.parent.name, "reason": "incomplete_packet_trial"})
            continue
        rows.append(
            {
                **analyze_trial(file.parent),
                "trial_valid": detail.get("result", {}).get("valid"),
                "trial_passed": detail.get("result", {}).get("passed"),
                "harness_reason": detail.get("result", {}).get("reason"),
            }
        )
    return {
        "experiment": directory.name,
        "evidence_level": "real_filtered_underlay_captures",
        "trials": rows,
        "excluded_trials": excluded,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError("Existing path evidence preserved; choose a fresh --output")
    result = analyze_experiment(args.experiment)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "experiment": result["experiment"],
                "trials_analyzed": len(result["trials"]),
                "excluded": len(result["excluded_trials"]),
            }
        )
    )
