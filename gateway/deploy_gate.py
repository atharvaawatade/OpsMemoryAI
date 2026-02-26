#!/usr/bin/env python3
"""
OpsMemory AI — Deploy Gate (entry point wrapper)
=================================================
Delegates to ci_agent.py which contains the real Agent Builder integration.
This file exists as a convenience alias — both entry points are identical.

Usage:
    python3 gateway/deploy_gate.py <service> <version> <change_description>

Example:
    python3 gateway/deploy_gate.py checkout-service 3.0.0 "Increased retry_count to 50"
"""

import sys
import os

# Add gateway directory to path so we can import ci_agent
sys.path.insert(0, os.path.dirname(__file__))

from ci_agent import main

if __name__ == "__main__":
    main()
