name: Modbus regression tests

on:
  push:
    branches:
      - main
      - modbus-robustness
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  unit-tests:
    name: Python unit tests
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Check out source code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Compile Python source
        run: python -m compileall -q custom_components tests

      - name: Run unit tests
        run: python -m unittest discover -s tests -v
