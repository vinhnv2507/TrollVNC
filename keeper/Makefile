TARGET := iphone:clang:16.5:14.0
ARCHS := arm64

include $(THEOS)/makefiles/common.mk

APPLICATION_NAME := ControlIOSKeeper

ControlIOSKeeper_FILES := main.m AppDelegate.m
ControlIOSKeeper_FRAMEWORKS := UIKit CoreGraphics CoreFoundation AVFoundation
ControlIOSKeeper_CFLAGS := -fobjc-arc
ControlIOSKeeper_CODESIGN_FLAGS := -Sentitlements.plist
ControlIOSKeeper_INSTALL_PATH := /Applications

include $(THEOS_MAKE_PATH)/application.mk

# Đóng gói .tipa (Payload/…) để cài bằng TrollStore.
after-stage::
	@echo "Đã dựng .app trong $(THEOS_STAGING_DIR)/Applications/$(APPLICATION_NAME).app"

tipa:: stage
	@rm -rf packages/tipa && mkdir -p packages/tipa/Payload
	@cp -R "$(THEOS_STAGING_DIR)/Applications/$(APPLICATION_NAME).app" packages/tipa/Payload/
	@cd packages/tipa && zip -qr "../$(APPLICATION_NAME).tipa" Payload
	@echo "Đã tạo packages/$(APPLICATION_NAME).tipa — cài bằng TrollStore."
