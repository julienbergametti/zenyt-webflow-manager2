#!/usr/bin/env python3
"""
Quick launcher for Zenyt Lead Manager
Run: python3 run.py
"""
import subprocess
import sys
import os

def main():
    # Change to script directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    print("🚀 Starting Zenyt Lead Manager...")
    print("   Dashboard: http://localhost:3000")
    print("   Press Ctrl+C to stop\n")
    
    # Run the dashboard
    subprocess.run([sys.executable, "dashboard/app.py"])

if __name__ == "__main__":
    main()
