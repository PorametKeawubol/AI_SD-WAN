// Run from vendor/fleximanage/backend with local lab environment set.
// This pinned upstream migration is required even for an empty database:
// organization creation reads the default notification rules it inserts.
const path = require('path');
const backend = path.resolve(process.cwd());
async function seed() {
  await require(path.join(backend, 'migrations/1673441687367-add_default_notifications_conf.js')).up();
  // Upstream assumes an imported-app document exists when installing policies.
  // Keep an existing catalog; a fresh isolated lab starts with explicit custom apps.
  const { importedAppIdentifications } = require(path.join(backend, 'models/appIdentifications.js'));
  await importedAppIdentifications.updateOne({}, {
    $setOnInsert: { meta: { time: 0 }, appIdentifications: [], imported: [] }
  }, { upsert: true });
}
seed()
  .then(() => process.exit(0))
  .catch(() => process.exit(1));
