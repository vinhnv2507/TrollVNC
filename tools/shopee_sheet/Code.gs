/**
 * Shopee Cookie Exporter for Google Sheets.
 *
 * Cookie được lưu trong UserProperties của chính tài khoản Google đang chạy,
 * không ghi vào ô hoặc nhật ký. Không chia sẻ Sheet/Script khi còn lưu cookie.
 */

var SHEETS = {
  CONFIG: 'Cấu hình',
  ACCOUNT: 'Tài khoản',
  ADDRESS: 'Địa chỉ',
  ORDERS: 'Đơn hàng',
  SHIPPING: 'Vận chuyển',
  API_LOG: 'Nhật ký API'
};

var PROP_COOKIE = 'SHOPEE_COOKIE_HEADER';
var SHOPEE_ORIGIN = 'https://shopee.vn';
var SPX_ORIGIN = 'https://spx.vn';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Shopee Export')
    .addItem('1. Tạo/cập nhật mẫu Sheet', 'setupShopeeSheet')
    .addItem('2. Nhập/cập nhật cookie', 'promptShopeeCookie')
    .addItem('3. Lấy dữ liệu', 'fetchShopeeData')
    .addSeparator()
    .addItem('Kiểm tra cookie đã lưu', 'showCookieStatus')
    .addItem('Xóa cookie đã lưu', 'clearStoredCookie')
    .addToUi();
}

function setupShopeeSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var config = ensureSheet_(ss, SHEETS.CONFIG);
  var account = ensureSheet_(ss, SHEETS.ACCOUNT);
  var address = ensureSheet_(ss, SHEETS.ADDRESS);
  var orders = ensureSheet_(ss, SHEETS.ORDERS);
  var shipping = ensureSheet_(ss, SHEETS.SHIPPING);
  var apiLog = ensureSheet_(ss, SHEETS.API_LOG);

  config.clear();
  config.getRange('A1:B1').setValues([['SHOPEE COOKIE EXPORTER', 'Giá trị']]);
  config.getRange('A2:B5').setValues([
    ['Giới hạn đơn thử lấy', 50],
    ['Khu vực', 'VN'],
    ['Chế độ', 'Chỉ nhập cookie — tự dò Order ID và mã vận đơn nếu API phản hồi đủ dữ liệu'],
    ['Ghi chú', 'Nếu Shopee trả 90309999, cookie không đủ để lấy danh sách/chi tiết đơn; Sheet vẫn xuất account, địa chỉ và bộ đếm.']
  ]);
  config.getRange('A7:B12').setValues([
    ['Cách dùng', ''],
    ['Bước 1', 'Menu Shopee Export → Nhập/cập nhật cookie'],
    ['Bước 2', 'Menu Shopee Export → Lấy dữ liệu'],
    ['Tự động', 'Nếu lấy được danh sách/chi tiết đơn: tự tìm Order ID, ePOD và mã vận đơn có trong phản hồi → tracking SPX.'],
    ['Bảo mật', 'Không chia sẻ cookie. Khi dùng xong chọn Xóa cookie đã lưu.'],
    ['Giới hạn', 'Không có cách đảm bảo lấy toàn bộ đơn chỉ bằng cookie khi Shopee chặn 90309999.']
  ]);
  styleConfig_(config);

  initializeResultSheet_(account, ['Trường', 'Giá trị', 'Nguồn']);
  initializeResultSheet_(address, [
    'ID', 'Tên người nhận', 'Số điện thoại', 'Địa chỉ', 'Phường/Thành phố',
    'Tỉnh', 'Quốc gia', 'Địa chỉ đầy đủ', 'Latitude', 'Longitude', 'Mặc định giao hàng'
  ]);
  initializeResultSheet_(orders, [
    'Order ID', 'Mã đơn', 'Trạng thái', 'Shop', 'Sản phẩm', 'Tổng tiền',
    'Thanh toán', 'Đơn vị VC', 'Mã vận đơn', 'ePOD', 'Nguồn/Ghi chú'
  ]);
  initializeResultSheet_(shipping, [
    'Mã vận đơn', 'Mã SLS', 'Trạng thái', 'Mô tả', 'Thời gian',
    'Vị trí', 'Địa chỉ vị trí', 'Client Order ID'
  ]);
  initializeResultSheet_(apiLog, ['Thời gian', 'API', 'HTTP', 'Mã lỗi', 'Thông báo']);

  ss.setActiveSheet(config);
  SpreadsheetApp.getUi().alert(
    'Đã tạo mẫu. Tiếp theo chọn Shopee Export → Nhập/cập nhật cookie.'
  );
}

function promptShopeeCookie() {
  var ui = SpreadsheetApp.getUi();
  var result = ui.prompt(
    'Nhập cookie Shopee',
    'Dán toàn bộ dòng Cookie hoặc nội dung cookie-header.txt. Cookie sẽ lưu riêng trong UserProperties, không ghi vào Sheet.',
    ui.ButtonSet.OK_CANCEL
  );
  if (result.getSelectedButton() !== ui.Button.OK) return;

  var normalized = normalizeCookieHeader_(result.getResponseText());
  if (!normalized || normalized.indexOf('SPC_ST=') < 0) {
    ui.alert('Cookie không hợp lệ hoặc thiếu SPC_ST. Không lưu dữ liệu.');
    return;
  }
  PropertiesService.getUserProperties().setProperty(PROP_COOKIE, normalized);
  ui.alert('Đã lưu cookie cho tài khoản Google hiện tại. Không chia sẻ Sheet/Script này.');
}

function showCookieStatus() {
  var cookie = PropertiesService.getUserProperties().getProperty(PROP_COOKIE) || '';
  var map = parseCookieMap_(cookie);
  var status = cookie
    ? 'Đã lưu cookie. User ID: ' + (map.userid || map.SPC_U || 'không xác định') +
      '; username: ' + (map.username || 'không xác định') +
      '; có SPC_ST: ' + (map.SPC_ST ? 'có' : 'không')
    : 'Chưa lưu cookie.';
  SpreadsheetApp.getUi().alert(status);
}

function clearStoredCookie() {
  PropertiesService.getUserProperties().deleteProperty(PROP_COOKIE);
  SpreadsheetApp.getUi().alert('Đã xóa cookie đã lưu.');
}

function fetchShopeeData() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var cookie = PropertiesService.getUserProperties().getProperty(PROP_COOKIE) || '';
  if (!cookie) {
    SpreadsheetApp.getUi().alert('Chưa có cookie. Chọn Shopee Export → Nhập/cập nhật cookie.');
    return;
  }

  setupMissingSheets_(ss);
  clearResultRows_(ss);

  var config = ss.getSheetByName(SHEETS.CONFIG);
  var limit = parseInt(config.getRange('B2').getValue(), 10) || 50;
  limit = Math.max(1, Math.min(limit, 100));

  var ctx = {
    ss: ss,
    cookie: cookie,
    cookieMap: parseCookieMap_(cookie),
    logs: [],
    orderRows: [],
    trackingCandidates: [],
    epodByOrder: {},
    seenOrderIds: {}
  };

  try {
    writeAccountFromCookie_(ctx);
    fetchAddresses_(ctx);
    fetchOrderCounts_(ctx);
    fetchOrderList_(ctx, limit);

    // Khi API danh sách trả dữ liệu, tự dùng Order ID tìm chi tiết và ePOD.
    // Không yêu cầu người dùng nhập Order ID hoặc mã vận đơn.
    var discoveredOrderIds = [];
    for (var oi = 0; oi < ctx.orderRows.length; oi++) {
      if (ctx.orderRows[oi].orderId) discoveredOrderIds.push(String(ctx.orderRows[oi].orderId));
    }
    discoveredOrderIds = uniqueStrings_(discoveredOrderIds).slice(0, limit);
    for (var di = 0; di < discoveredOrderIds.length; di++) {
      fetchOrderDetail_(ctx, discoveredOrderIds[di]);
      fetchEpod_(ctx, discoveredOrderIds[di]);
      Utilities.sleep(150);
    }

    writeOrderRows_(ctx);

    var trackingNumbers = uniqueStrings_(ctx.trackingCandidates);
    for (var i = 0; i < trackingNumbers.length; i++) {
      fetchSpxTracking_(ctx, trackingNumbers[i]);
      Utilities.sleep(150);
    }

    writeApiLogs_(ctx);
    formatAllSheets_(ss);
    ss.toast('Đã hoàn tất lấy dữ liệu.', 'Shopee Export', 8);
  } catch (err) {
    ctx.logs.push(logRow_('Lỗi chương trình', '', 'EXCEPTION', safeMessage_(err)));
    writeApiLogs_(ctx);
    ss.toast('Có lỗi: ' + safeMessage_(err), 'Shopee Export', 12);
    throw err;
  }
}

function writeAccountFromCookie_(ctx) {
  var m = ctx.cookieMap;
  var rows = [
    ['username', m.username || '', 'cookie'],
    ['userid', m.userid || m.SPC_U || '', 'cookie'],
    ['shopid', m.shopid || '', 'cookie'],
    ['shopee_app_version', m.shopee_app_version || '', 'cookie'],
    ['shopee_rn_version', m.shopee_rn_version || '', 'cookie'],
    ['Có SPC_ST', m.SPC_ST ? 'Có' : 'Không', 'cookie (không hiển thị giá trị)']
  ];
  appendRows_(ctx.ss.getSheetByName(SHEETS.ACCOUNT), rows);
}

function fetchAddresses_(ctx) {
  var result = shopeeRequest_(ctx, '/api/v4/account/address/get_user_address_list', 'get');
  if (!isApiSuccess_(result.json)) return;

  var addresses = (((result.json || {}).data || {}).addresses) || [];
  var rows = [];
  for (var i = 0; i < addresses.length; i++) {
    var a = addresses[i] || {};
    var region = (((a.geoinfo || {}).region) || {});
    var full = [a.address, a.city, a.state, a.country].filter(Boolean).join(', ');
    rows.push([
      a.id || '',
      a.name || '',
      formatPhone_(a.phone || ''),
      a.address || '',
      a.city || a.district || a.town || '',
      a.state || '',
      a.country || '',
      full,
      region.latitude || '',
      region.longitude || '',
      a.is_delivery_address ? 'Có' : 'Không'
    ]);
  }
  appendRows_(ctx.ss.getSheetByName(SHEETS.ADDRESS), rows);
}

function fetchOrderCounts_(ctx) {
  var result = shopeeRequest_(ctx, '/api/v4/order/get_order_and_checkout_count', 'get');
  if (!isApiSuccess_(result.json)) return;
  var flat = [];
  flattenObject_((result.json || {}).data || {}, '', flat, 0);
  var rows = [];
  for (var i = 0; i < flat.length; i++) {
    rows.push(['order_count.' + flat[i][0], flat[i][1], 'Shopee API']);
  }
  appendRows_(ctx.ss.getSheetByName(SHEETS.ACCOUNT), rows);
}

function fetchOrderList_(ctx, limit) {
  var endpoints = [
    '/api/v4/order/get_all_order_and_checkout_list?limit=' + limit + '&offset=0&version=7',
    '/api/v4/order/get_order_list?list_type=8&offset=0&limit=' + limit + '&version=7',
    '/api/v4/order/get_order_list?list_type=7&offset=0&limit=' + limit + '&version=7'
  ];

  var found = 0;
  var blocked = false;
  for (var i = 0; i < endpoints.length; i++) {
    var result = shopeeRequest_(ctx, endpoints[i], 'get');
    var err = apiErrorCode_(result.json);
    if (String(err) === '90309999') blocked = true;
    if (!isApiSuccess_(result.json)) continue;

    var candidates = [];
    collectOrderObjects_((result.json || {}).data, candidates, 0);
    for (var j = 0; j < candidates.length; j++) {
      addOrderObject_(ctx, candidates[j], 'API danh sách đơn');
      found++;
    }
  }

  if (!found && blocked) {
    appendRows_(ctx.ss.getSheetByName(SHEETS.ORDERS), [[
      '', '', 'Bị chặn 90309999', '', '', '', '', '', '', '',
      'Shopee chặn API danh sách/chi tiết đơn (90309999). Cookie đơn thuần không thể tự suy ra Order ID hoặc mã vận đơn.'
    ]]);
  }
}

function fetchOrderDetail_(ctx, orderId) {
  var result = shopeeRequest_(
    ctx,
    '/api/v4/order/get_order_detail?order_id=' + encodeURIComponent(orderId),
    'get'
  );
  if (!isApiSuccess_(result.json)) return;
  var candidates = [];
  collectOrderObjects_((result.json || {}).data, candidates, 0);
  for (var i = 0; i < candidates.length; i++) {
    addOrderObject_(ctx, candidates[i], 'API chi tiết đơn');
  }
  collectTrackingNumbers_((result.json || {}).data, ctx.trackingCandidates, 0);
}

function fetchEpod_(ctx, orderId) {
  var path = '/api/v4/order/buyer/get_standard_epod_link?order_id=' +
    encodeURIComponent(orderId) + '&forder_id=&ref_id=';
  var result = shopeeRequest_(ctx, path, 'get');
  if (!isApiSuccess_(result.json)) return;
  var data = (result.json || {}).data || {};
  var url = data.std_epod || ((data.std_epod_links || [])[0]) || '';
  if (!url) return;
  ctx.epodByOrder[String(orderId)] = url;
  for (var i = 0; i < ctx.orderRows.length; i++) {
    if (String(ctx.orderRows[i].orderId) === String(orderId)) {
      ctx.orderRows[i].epod = url;
    }
  }
}

function addOrderObject_(ctx, obj, source) {
  if (!obj || typeof obj !== 'object') return;
  var orderId = firstValue_(obj, ['order_id', 'orderid', 'orderId', 'id']);
  var orderSn = firstValue_(obj, ['order_sn', 'ordersn', 'order_no', 'order_number', 'checkout_sn']);
  if (!orderId && !orderSn) return;

  var key = String(orderId || orderSn);
  var existing = null;
  for (var i = 0; i < ctx.orderRows.length; i++) {
    if (String(ctx.orderRows[i].orderId || ctx.orderRows[i].orderSn) === key) {
      existing = ctx.orderRows[i];
      break;
    }
  }
  if (!existing) {
    existing = {
      orderId: orderId || '', orderSn: orderSn || '', status: '', shop: '', item: '',
      total: '', payment: '', carrier: '', tracking: '', epod: '', note: source
    };
    ctx.orderRows.push(existing);
  }
  ctx.seenOrderIds[key] = true;

  existing.orderId = existing.orderId || orderId || '';
  existing.orderSn = existing.orderSn || orderSn || '';
  existing.status = existing.status || firstValue_(obj, [
    'order_status_text', 'order_status', 'status_text', 'status', 'shipping_status'
  ]) || '';
  existing.shop = existing.shop || firstValue_(obj, [
    'shop_name', 'shopname', 'seller_name', 'username'
  ]) || '';
  existing.item = existing.item || firstValueDeep_(obj, [
    'item_name', 'name', 'product_name', 'model_name'
  ], 0) || '';
  existing.total = existing.total || firstValue_(obj, [
    'total_price', 'actual_price', 'buyer_pay_amount', 'paid_amount'
  ]) || '';
  existing.payment = existing.payment || firstValue_(obj, [
    'payment_channel_name', 'payment_method_name', 'payment_method'
  ]) || '';
  existing.carrier = existing.carrier || firstValue_(obj, [
    'shipping_carrier', 'actual_shipping_carrier', 'fulfilment_carrier', 'carrier'
  ]) || '';
  existing.tracking = existing.tracking || firstValueDeep_(obj, [
    'tracking_number', 'tracking_no', 'shipping_traceno', 'spx_tn', 'sls_tn', 'tracking_id'
  ], 0) || '';
  if (existing.tracking) ctx.trackingCandidates.push(String(existing.tracking));
}

function writeOrderRows_(ctx) {
  var rows = [];
  for (var i = 0; i < ctx.orderRows.length; i++) {
    var o = ctx.orderRows[i];
    var epod = o.epod || ctx.epodByOrder[String(o.orderId)] || '';
    rows.push([
      o.orderId, o.orderSn, scalarForCell_(o.status), o.shop, o.item,
      scalarForCell_(o.total), scalarForCell_(o.payment), o.carrier,
      o.tracking, epod, o.note
    ]);
  }
  appendRows_(ctx.ss.getSheetByName(SHEETS.ORDERS), rows);
}

function fetchSpxTracking_(ctx, trackingNo) {
  var no = String(trackingNo || '').trim().toUpperCase();
  if (!no) return;
  var url = SPX_ORIGIN + '/shipment/order/open/order/get_order_info?spx_tn=' +
    encodeURIComponent(no) + '&language_code=vi';
  var response = UrlFetchApp.fetch(url, {
    method: 'get',
    muteHttpExceptions: true,
    followRedirects: true,
    headers: {
      'User-Agent': browserUserAgent_(),
      'Accept': 'application/json',
      'Referer': SPX_ORIGIN + '/tracking',
      'Origin': SPX_ORIGIN
    }
  });
  var status = response.getResponseCode();
  var json = parseJson_(response.getContentText());
  var retcode = json ? json.retcode : 'non-json';
  ctx.logs.push(logRow_('SPX tracking ' + maskTracking_(no), status, retcode, (json || {}).message || ''));
  if (!json || json.retcode !== 0) return;

  var sls = (((json.data || {}).sls_tracking_info) || {});
  var records = sls.records || [];
  var rows = [];
  for (var i = 0; i < records.length; i++) {
    var r = records[i] || {};
    var loc = r.current_location || {};
    rows.push([
      no,
      sls.sls_tn || '',
      r.tracking_name || '',
      r.buyer_description || r.description || '',
      formatUnixTime_(r.actual_time),
      loc.location_name || '',
      loc.full_address || '',
      sls.client_order_id || ''
    ]);
  }
  if (!rows.length) {
    rows.push([no, sls.sls_tn || '', '', 'Không có lịch sử', '', '', '', sls.client_order_id || '']);
  }
  appendRows_(ctx.ss.getSheetByName(SHEETS.SHIPPING), rows);
}

function shopeeRequest_(ctx, path, method, payload) {
  var url = path.indexOf('http') === 0 ? path : SHOPEE_ORIGIN + path;
  var options = {
    method: method || 'get',
    muteHttpExceptions: true,
    followRedirects: true,
    headers: {
      'User-Agent': browserUserAgent_(),
      'Accept': 'application/json, text/plain, */*',
      'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
      'Cookie': ctx.cookie,
      'X-API-SOURCE': 'pc',
      'X-Shopee-Language': 'vi',
      'X-Requested-With': 'XMLHttpRequest',
      'Referer': SHOPEE_ORIGIN + '/'
    }
  };
  if (payload !== undefined && payload !== null) {
    options.contentType = 'application/json';
    options.payload = JSON.stringify(payload);
  }

  var response = UrlFetchApp.fetch(url, options);
  var status = response.getResponseCode();
  var text = response.getContentText();
  var json = parseJson_(text);
  var error = apiErrorCode_(json);
  var message = apiErrorMessage_(json);
  ctx.logs.push(logRow_(sanitizeApiPath_(path), status, error, message));
  return {status: status, json: json, text: text};
}

function collectOrderObjects_(obj, out, depth) {
  if (depth > 8 || obj === null || obj === undefined) return;
  if (Array.isArray(obj)) {
    for (var i = 0; i < obj.length; i++) collectOrderObjects_(obj[i], out, depth + 1);
    return;
  }
  if (typeof obj !== 'object') return;

  var keys = Object.keys(obj);
  var hasOrderIdentity = keys.some(function(k) {
    return ['order_id', 'orderid', 'orderId', 'order_sn', 'ordersn', 'order_no'].indexOf(k) >= 0;
  });
  if (hasOrderIdentity) out.push(obj);

  for (var j = 0; j < keys.length; j++) {
    var v = obj[keys[j]];
    if (v && typeof v === 'object') collectOrderObjects_(v, out, depth + 1);
  }
}

function collectTrackingNumbers_(obj, out, depth) {
  if (depth > 10 || obj === null || obj === undefined) return;
  if (Array.isArray(obj)) {
    for (var i = 0; i < obj.length; i++) collectTrackingNumbers_(obj[i], out, depth + 1);
    return;
  }
  if (typeof obj !== 'object') return;
  var target = {
    tracking_number: true, tracking_no: true, shipping_traceno: true,
    spx_tn: true, sls_tn: true, tracking_id: true
  };
  var keys = Object.keys(obj);
  for (var j = 0; j < keys.length; j++) {
    var k = keys[j];
    var v = obj[k];
    if (target[k] && (typeof v === 'string' || typeof v === 'number')) out.push(String(v));
    if (v && typeof v === 'object') collectTrackingNumbers_(v, out, depth + 1);
  }
}

function firstValueDeep_(obj, names, depth) {
  if (depth > 7 || obj === null || obj === undefined) return '';
  if (Array.isArray(obj)) {
    for (var i = 0; i < obj.length; i++) {
      var av = firstValueDeep_(obj[i], names, depth + 1);
      if (av !== '' && av !== null && av !== undefined) return av;
    }
    return '';
  }
  if (typeof obj !== 'object') return '';
  var direct = firstValue_(obj, names);
  if (direct !== '' && direct !== null && direct !== undefined) return direct;
  var keys = Object.keys(obj);
  for (var j = 0; j < keys.length; j++) {
    var v = obj[keys[j]];
    if (v && typeof v === 'object') {
      var found = firstValueDeep_(v, names, depth + 1);
      if (found !== '' && found !== null && found !== undefined) return found;
    }
  }
  return '';
}

function firstValue_(obj, names) {
  if (!obj || typeof obj !== 'object') return '';
  for (var i = 0; i < names.length; i++) {
    if (Object.prototype.hasOwnProperty.call(obj, names[i])) {
      var v = obj[names[i]];
      if (v !== null && v !== undefined && v !== '') return v;
    }
  }
  return '';
}

function flattenObject_(obj, prefix, out, depth) {
  if (depth > 6) return;
  if (obj === null || obj === undefined || typeof obj !== 'object') {
    out.push([prefix || 'value', scalarForCell_(obj)]);
    return;
  }
  if (Array.isArray(obj)) {
    out.push([prefix + '.length', obj.length]);
    return;
  }
  var keys = Object.keys(obj);
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    var next = prefix ? prefix + '.' + k : k;
    var v = obj[k];
    if (v && typeof v === 'object') flattenObject_(v, next, out, depth + 1);
    else out.push([next, scalarForCell_(v)]);
  }
}

function normalizeCookieHeader_(raw) {
  var text = String(raw || '').trim();
  text = text.replace(/^cookie\s*:\s*/i, '');
  text = text.replace(/[\r\n]+/g, ' ').trim();
  return text;
}

function parseCookieMap_(cookie) {
  var out = {};
  var parts = String(cookie || '').split(';');
  for (var i = 0; i < parts.length; i++) {
    var p = parts[i].trim();
    if (!p) continue;
    var eq = p.indexOf('=');
    if (eq <= 0) continue;
    var key = p.slice(0, eq).trim();
    var value = p.slice(eq + 1).trim();
    out[key] = value;
  }
  return out;
}

function formatPhone_(phone) {
  var p = String(phone || '').replace(/\s+/g, '');
  if (p.indexOf('84') === 0) return '+' + p;
  return p;
}

function formatUnixTime_(seconds) {
  if (!seconds) return '';
  var date = new Date(Number(seconds) * 1000);
  return Utilities.formatDate(date, 'Asia/Ho_Chi_Minh', 'yyyy-MM-dd HH:mm:ss');
}

function apiErrorCode_(json) {
  if (!json || typeof json !== 'object') return 'non-json';
  if (Object.prototype.hasOwnProperty.call(json, 'error')) return json.error;
  if (Object.prototype.hasOwnProperty.call(json, 'retcode')) return json.retcode;
  return '';
}

function apiErrorMessage_(json) {
  if (!json || typeof json !== 'object') return '';
  return json.error_msg || json.message || json.msg || '';
}

function isApiSuccess_(json) {
  if (!json || typeof json !== 'object') return false;
  var e = apiErrorCode_(json);
  return e === 0 || e === '0' || e === '' || e === null || e === undefined;
}

function parseJson_(text) {
  try { return JSON.parse(text); } catch (e) { return null; }
}

function scalarForCell_(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') {
    try { return JSON.stringify(value); } catch (e) { return String(value); }
  }
  return value;
}

function cleanScalar_(value) {
  if (value === null || value === undefined) return '';
  var s = String(value).trim();
  return s;
}

function uniqueStrings_(items) {
  var seen = {};
  var out = [];
  for (var i = 0; i < items.length; i++) {
    var value = String(items[i] || '').trim();
    if (!value || seen[value]) continue;
    seen[value] = true;
    out.push(value);
  }
  return out;
}

function browserUserAgent_() {
  return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
}

function logRow_(api, http, error, message) {
  return [
    Utilities.formatDate(new Date(), 'Asia/Ho_Chi_Minh', 'yyyy-MM-dd HH:mm:ss'),
    api,
    http,
    error === undefined || error === null ? '' : error,
    String(message || '').slice(0, 500)
  ];
}

function sanitizeApiPath_(path) {
  var text = String(path || '');
  text = text.replace(/([?&](?:token|cookie|spc_st|authorization)=)[^&]*/ig, '$1<redacted>');
  return text.slice(0, 500);
}

function maskTracking_(value) {
  var s = String(value || '');
  if (s.length <= 8) return s;
  return s.slice(0, 5) + '…' + s.slice(-4);
}

function safeMessage_(err) {
  if (!err) return 'Lỗi không xác định';
  return String(err.message || err).replace(/SPC_ST=[^;\s]+/ig, 'SPC_ST=<redacted>');
}

function ensureSheet_(ss, name) {
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

function initializeResultSheet_(sheet, headers) {
  sheet.clear();
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  sheet.setFrozenRows(1);
  sheet.getRange(1, 1, 1, headers.length)
    .setFontWeight('bold')
    .setBackground('#ee4d2d')
    .setFontColor('#ffffff');
}

function styleConfig_(sheet) {
  sheet.setFrozenRows(1);
  sheet.getRange('A1:B1').setFontWeight('bold').setBackground('#ee4d2d').setFontColor('#ffffff');
  sheet.getRange('A7:B7').setFontWeight('bold').setBackground('#fce8e3');
  sheet.setColumnWidth(1, 230);
  sheet.setColumnWidth(2, 620);
  sheet.getDataRange().setVerticalAlignment('top').setWrap(true);
}

function setupMissingSheets_(ss) {
  if (!ss.getSheetByName(SHEETS.CONFIG)) setupShopeeSheet();
  if (!ss.getSheetByName(SHEETS.ACCOUNT)) initializeResultSheet_(ensureSheet_(ss, SHEETS.ACCOUNT), ['Trường', 'Giá trị', 'Nguồn']);
  if (!ss.getSheetByName(SHEETS.ADDRESS)) initializeResultSheet_(ensureSheet_(ss, SHEETS.ADDRESS), ['ID', 'Tên người nhận', 'Số điện thoại', 'Địa chỉ', 'Phường/Thành phố', 'Tỉnh', 'Quốc gia', 'Địa chỉ đầy đủ', 'Latitude', 'Longitude', 'Mặc định giao hàng']);
  if (!ss.getSheetByName(SHEETS.ORDERS)) initializeResultSheet_(ensureSheet_(ss, SHEETS.ORDERS), ['Order ID', 'Mã đơn', 'Trạng thái', 'Shop', 'Sản phẩm', 'Tổng tiền', 'Thanh toán', 'Đơn vị VC', 'Mã vận đơn', 'ePOD', 'Nguồn/Ghi chú']);
  if (!ss.getSheetByName(SHEETS.SHIPPING)) initializeResultSheet_(ensureSheet_(ss, SHEETS.SHIPPING), ['Mã vận đơn', 'Mã SLS', 'Trạng thái', 'Mô tả', 'Thời gian', 'Vị trí', 'Địa chỉ vị trí', 'Client Order ID']);
  if (!ss.getSheetByName(SHEETS.API_LOG)) initializeResultSheet_(ensureSheet_(ss, SHEETS.API_LOG), ['Thời gian', 'API', 'HTTP', 'Mã lỗi', 'Thông báo']);
}

function clearResultRows_(ss) {
  var names = [SHEETS.ACCOUNT, SHEETS.ADDRESS, SHEETS.ORDERS, SHEETS.SHIPPING, SHEETS.API_LOG];
  for (var i = 0; i < names.length; i++) {
    var sheet = ss.getSheetByName(names[i]);
    if (sheet && sheet.getLastRow() > 1) {
      sheet.getRange(2, 1, sheet.getLastRow() - 1, Math.max(1, sheet.getLastColumn())).clearContent();
    }
  }
}

function appendRows_(sheet, rows) {
  if (!sheet || !rows || !rows.length) return;
  var width = rows[0].length;
  sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, width).setValues(rows);
}

function writeApiLogs_(ctx) {
  appendRows_(ctx.ss.getSheetByName(SHEETS.API_LOG), ctx.logs);
}

function formatAllSheets_(ss) {
  var names = [SHEETS.ACCOUNT, SHEETS.ADDRESS, SHEETS.ORDERS, SHEETS.SHIPPING, SHEETS.API_LOG];
  for (var i = 0; i < names.length; i++) {
    var sheet = ss.getSheetByName(names[i]);
    if (!sheet) continue;
    sheet.getDataRange().setVerticalAlignment('top').setWrap(true);
    if (sheet.getLastColumn() > 0) sheet.autoResizeColumns(1, sheet.getLastColumn());
    for (var c = 1; c <= sheet.getLastColumn(); c++) {
      if (sheet.getColumnWidth(c) > 420) sheet.setColumnWidth(c, 420);
    }
  }
}
