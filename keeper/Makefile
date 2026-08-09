TARGET := iphone:clang:16.5:14.0
ARCHS := arm64

include $(THEOS)/makefiles/common.mk

# ---- Daemon nền độc lập ----
TOOL_NAME := keeperd
keeperd_FILES := daemon/main.m
keeperd_FRAMEWORKS := Foundation CoreFoundation
keeperd_CFLAGS := -fobjc-arc
keeperd_CODESIGN_FLAGS := -Sdaemon/entitlements.plist
keeperd_INSTALL_PATH := /usr/local/bin

# ---- App bệ phóng ----
APPLICATION_NAME := ControlIOSKeeper
ControlIOSKeeper_FILES := main.m AppDelegate.m
ControlIOSKeeper_FRAMEWORKS := UIKit CoreGraphics CoreFoundation BackgroundTasks
ControlIOSKeeper_CFLAGS := -fobjc-arc
ControlIOSKeeper_CODESIGN_FLAGS := -Sentitlements.plist
ControlIOSKeeper_INSTALL_PATH := /Applications

include $(THEOS_MAKE_PATH)/tool.mk
include $(THEOS_MAKE_PATH)/application.mk

# Nhét keeperd (đã ký entitlements, lấy từ staging install path) vào bundle app.
after-stage::
	@cp "$(THEOS_STAGING_DIR)/usr/local/bin/keeperd" \
	    "$(THEOS_STAGING_DIR)/Applications/ControlIOSKeeper.app/keeperd"
	@chmod 755 "$(THEOS_STAGING_DIR)/Applications/ControlIOSKeeper.app/keeperd"
	@echo "Đã nhét keeperd vào ControlIOSKeeper.app"

# Đóng gói .tipa để cài bằng TrollStore.
tipa:: stage
	@rm -rf packages/tipa && mkdir -p packages/tipa/Payload
	@cp -R "$(THEOS_STAGING_DIR)/Applications/ControlIOSKeeper.app" packages/tipa/Payload/
	@cd packages/tipa && zip -qr "../ControlIOSKeeper.tipa" Payload
	@echo "Đã tạo packages/ControlIOSKeeper.tipa — cài bằng TrollStore."
