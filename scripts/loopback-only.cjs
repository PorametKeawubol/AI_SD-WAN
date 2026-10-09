// Native lab controller only. Keep the unmodified upstream controller off the LAN.
const net = require('node:net');
const original = net.Server.prototype.listen;
net.Server.prototype.listen = function (...args) {
  if (typeof args[0] === 'number' || (typeof args[0] === 'string' && /^\d+$/.test(args[0]))) {
    const callback = args.find(value => typeof value === 'function');
    return original.call(this, Number(args[0]), '127.0.0.1', callback);
  }
  return original.apply(this, args);
};
