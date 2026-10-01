#!/bin/sh
# Build and run the firmware host tests.
set -e
cd "$(dirname "$0")"
sed -e 's#^\#include "config.h"#// config.h provided by the test harness#' \
    -e 's#^\#include <\(WiFi\|PubSubClient\|Preferences\|HX711\|time\)\.h>#// &#' \
    ../shelf_node.ino > firmware_under_test.inc
g++ -std=c++17 -Wall -Wno-unused-variable -I. -include stubs.h run_tests.cpp -o run_tests
./run_tests
