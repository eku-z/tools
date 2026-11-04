"""Talking to DaVinci Resolve Studio through its scripting library.

Every call here runs on the window's thread (ZCuts' rule: never touch
Resolve from a helper thread), and any call may answer None or fail
quietly, so nothing assumes a project, timeline or clip exists.
"""
