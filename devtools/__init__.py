"""Development and demo tooling.

Not part of the package and not a test suite: these are the pieces the local
Compose demo and manual experiments need. They live here rather than under
``tests/`` because a runnable service is not a test, and pointing a Compose
service at a test module makes the test namespace a deployment dependency.
"""
