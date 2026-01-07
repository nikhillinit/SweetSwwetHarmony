#!/usr/bin/env python
"""
Launch the Discovery Engine Dashboard.

Usage:
    python run_dashboard.py

Or directly with streamlit:
    streamlit run dashboard/app.py
"""

import subprocess
import sys


def main():
    print("Starting Discovery Engine Dashboard...")
    print("Open http://localhost:8501 in your browser")
    print("Press Ctrl+C to stop\n")

    subprocess.run([
        sys.executable, "-m", "streamlit", "run",
        "dashboard/app.py",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ])


if __name__ == "__main__":
    main()
