# echoneura-satellite builds
#
#   make native    -> ./satellite          (this machine; test with --file)
#   make cross     -> ./satellite-arm      (static ARMv7 musl, for the Dot)
#   make clean
#
# The cross toolchain (musl.cc, ~110 MB) is downloaded once into .cache/
# which is gitignored. Nothing binary is committed to the repo.

CC       ?= cc
CFLAGS   ?= -O2 -Wall -Wextra -std=gnu11
INC       = -Ivendor/tinyalsa/include
SRC       = satellite.c vendor/tinyalsa/src/pcm.c vendor/tinyalsa/src/limits.c

CROSS_TRIPLE = armv7l-linux-musleabihf
CROSS_CC     = .cache/$(CROSS_TRIPLE)-cross/bin/$(CROSS_TRIPLE)-gcc
TOOLCHAIN_URL = https://musl.cc/$(CROSS_TRIPLE)-cross.tgz
# zig ships a complete musl cross toolchain: prefer it when present
ZIGCC        := $(shell command -v zig >/dev/null 2>&1 && echo "zig cc" || (python3 -c "import ziglang" 2>/dev/null && echo "python3 -m ziglang cc"))

.PHONY: native cross cross-gcc clean toolchain

native: satellite
	@echo "built ./satellite (try: ./satellite --file ../../../data/samples/demo.wav --fast)"

satellite: $(SRC) satellite.c
	$(CC) $(CFLAGS) $(INC) -o $@ $(SRC)

toolchain: $(CROSS_CC)

$(CROSS_CC):
	@mkdir -p .cache
	@echo "downloading $(TOOLCHAIN_URL) …"
	@curl -fL --retry 3 -o .cache/toolchain.tgz $(TOOLCHAIN_URL)
	@tar -xzf .cache/toolchain.tgz -C .cache
	@rm -f .cache/toolchain.tgz
	@touch $@

cross:
ifeq ($(strip $(ZIGCC)),)
	@echo "no zig toolchain: run  pip install ziglang  (or install system zig), or use: make cross-gcc"; exit 1
endif
	$(ZIGCC) -O2 -std=gnu11 -static $(INC) -o satellite-arm $(SRC) -target arm-linux-musleabihf
	@echo "built ./satellite-arm (static ARMv7 — adb push it to /data/local/tmp/)"

# fallback when zig is unavailable: musl.cc cross gcc (110 MB download)
cross-gcc: $(CROSS_CC)
	$(CROSS_CC) -O2 -Wall -Wextra -std=gnu11 -static $(INC) -o satellite-arm $(SRC)
	@echo "built ./satellite-arm (static ARMv7 — adb push it to /data/local/tmp/)"

clean:
	rm -f satellite satellite-arm
