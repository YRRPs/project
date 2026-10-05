#!/bin/sh
set -eu
exec python3 -m unittest -v tests.test_deploy_ota_release
