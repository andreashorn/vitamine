#!/bin/sh
set -eu

promo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
npm --prefix "$promo_root/promo-video" run render
