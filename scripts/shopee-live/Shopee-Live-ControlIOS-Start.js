// Bộ nạp Shopee Live ControlIOS 1.1.0.
var path = "/var/mobile/Media/ControlIOS/Shopee-Live-ControlIOS.js";
var code = readFile(path);
if (!code) {
    log("Chưa có file script: " + path);
    stop();
}
eval(code);
