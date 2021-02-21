#!/usr/bin/env bash
# This should be run in the Debian chroot

LTP=$(dirname "$(readlink -f "${0}")")/ltp
MAKE=(make -skj"$(nproc)")

set -x

git clone --depth=1 https://github.com/linux-test-project/ltp "${LTP}"
cd "${LTP}" || exit ${?}

"${MAKE[@]}" autotools
./configure

TEST_CASES=(
    kernel/fs/proc
    kernel/fs/read_all
    lib
)
for TEST_CASE in "${TEST_CASES[@]}"; do
    cd "${LTP}"/testcases/"${TEST_CASE}" || exit ${?}
    "${MAKE[@]}"
done
