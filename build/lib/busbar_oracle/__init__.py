# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""busbar-oracle: record a released binary's exact behaviour per cell, replay it against a later one.

The package is deliberately thin. The recorder, normalizer, differ and replayer are
shipped as the files that produced every existing golden -- they are data about how a
recording was made as much as they are code -- and `busbar_oracle.cli` dispatches to
them. See cli.py for why nothing here reimplements them.
"""

__version__ = "0.1.0"
