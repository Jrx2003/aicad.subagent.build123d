from __future__ import annotations

from demo_enclosure_body_lid import export_demo as export_enclosure_demo
from demo_half_shell_directional_holes import export_demo as export_half_shell_demo
from demo_local_frame_countersink import export_demo as export_countersink_demo
from common import write_suite_summary


def main() -> None:
    entries = [
        export_countersink_demo(),
        export_half_shell_demo(),
        *export_enclosure_demo(),
    ]
    summary_path = write_suite_summary(entries)
    print(f"Wrote {summary_path.relative_to(summary_path.parent.parent)}")
    for entry in entries:
        bbox = entry["bbox"]
        print(f"- {entry['stem']}: volume={entry['volume']} bbox={bbox}")


if __name__ == "__main__":
    main()
