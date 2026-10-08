// Bộ nạp Shopee Live ControlIOS 1.1.1.
// true: dùng live đang mở; false: mở lại Shopee theo mã gốc.
var startFromCurrent = true;
var path = "/var/mobile/Media/ControlIOS/Shopee-Live-ControlIOS.js";
var code = readFile(path);
if (!code) {
    log("Chưa có file script: " + path);
    stop();
}
eval(startFromCurrent ? code.replace("START_FROM_CURRENT: false", "START_FROM_CURRENT: true") : code);
