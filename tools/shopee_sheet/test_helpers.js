const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync('tools/shopee_sheet/Code.gs', 'utf8');
const sandbox = {};

assert(!code.includes('manualOrderId'), 'no manual order id input');
assert(!code.includes('manualTracking'), 'no manual tracking input');
assert(!code.includes("getRange('B3')"), 'no tracking config cell');
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
function assert(cond, msg) { if (!cond) throw new Error(msg); }
const normalized = sandbox.normalizeCookieHeader_('Cookie: SPC_ST=abc==; SPC_U=1429; username=test\r\n');
assert(normalized === 'SPC_ST=abc==; SPC_U=1429; username=test', 'normalize cookie');
const parsed = sandbox.parseCookieMap_(normalized);
assert(parsed.SPC_ST === 'abc==', 'preserve equals in token');
assert(parsed.SPC_U === '1429', 'parse user');
assert(sandbox.formatPhone_('84567699734') === '+84567699734', 'format phone');
const orders = [];
sandbox.collectOrderObjects_({data:{orders:[{order_id:123,order_sn:'ABC'}]}}, orders, 0);
assert(orders.length === 1 && orders[0].order_id === 123, 'collect orders');
const tracks = [];
sandbox.collectTrackingNumbers_({parcel:{spx_tn:'SPXVN001'}}, tracks, 0);
assert(tracks[0] === 'SPXVN001', 'collect tracking');
console.log('Pure helper tests: OK');

const fakeMessage = 'Ki?n h?ng <b>SPXVN012345678901</b> c?a ??n h?ng <b>260101ABCDEF12</b> ?? giao th?nh c?ng.';
const fakeHex = Buffer.from(fakeMessage, 'utf8').toString('hex');
const decoded = sandbox.decodePossiblyHex_(fakeHex);
assert(decoded === fakeMessage, 'decode notification hex');
const plain = sandbox.stripHtml_(decoded);
assert(sandbox.extractTrackingNumber_(plain) === 'SPXVN012345678901', 'extract SPX tracking');
assert(sandbox.extractOrderSn_(plain, 'SPXVN012345678901') === '260101ABCDEF12', 'extract order sn');
assert(sandbox.extractOrderId_('rn/ORDER_DETAIL?orderId=243100000000001&utm=x') === '243100000000001', 'extract order id');

const notificationMessage = 'Ki?n h?ng <b>SPXVN012345678901</b> c?a ??n h?ng <b>260101ABCDEF12</b> ?? giao th?nh c?ng.';
const notificationAction = {
  title: Buffer.from('Giao ki?n h?ng th?nh c?ng', 'utf8').toString('hex'),
  content: Buffer.from(notificationMessage, 'utf8').toString('hex'),
  action_redirect_url: 'rn/ORDER_DETAIL?orderId=243100000000001&utm=x',
  createtime: 1789628303
};
sandbox.Utilities = {formatDate: () => '2026-09-17 13:58:23'};
sandbox.shopeeRequest_ = () => ({json: {data: {actions: [notificationAction]}}});
const notificationCtx = {orderRows: [], trackingCandidates: [], seenOrderIds: {}};
sandbox.fetchOrderNotifications_(notificationCtx, 50);
assert(notificationCtx.orderRows.length === 1, 'notification creates order');
assert(notificationCtx.orderRows[0].orderId === '243100000000001', 'notification order id');
assert(notificationCtx.orderRows[0].orderSn === '260101ABCDEF12', 'notification order sn');
assert(notificationCtx.orderRows[0].tracking === 'SPXVN012345678901', 'notification tracking');
assert(notificationCtx.trackingCandidates[0] === 'SPXVN012345678901', 'notification tracking candidate');
