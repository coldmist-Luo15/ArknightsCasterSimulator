// Read timing only with the frozen official Spine 3.8 runtime. Placeholder atlas
// regions satisfy attachment construction; no geometry/rendering is exported.
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const [runtimePath, skeletonPath, outputPath] = process.argv.slice(2);
if (!outputPath) throw new Error('Usage: node read_spine_events.cjs runtime.js input.skel output.json');
const code = fs.readFileSync(runtimePath);
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
if (hash(code) !== 'f1e0a31b9906e4d4daf2733857d21381ddbbe75adec7f4d83e1cc9b2b070dfc1')
  throw new Error('Unexpected Spine runtime snapshot');
const context = {};
vm.runInNewContext(code.toString('utf8'), context, {timeout: 10000});
const spine = context.spine;
const loader = {};
for (const kind of ['Region', 'Mesh', 'BoundingBox', 'Path', 'Point', 'Clipping']) {
  loader[`new${kind}Attachment`] = (_skin, name) => {
    const attachment = new spine[`${kind}Attachment`](name);
    if (kind === 'Region' || kind === 'Mesh')
      attachment.region = {originalWidth: 1, originalHeight: 1, width: 1, height: 1,
                           offsetX: 0, offsetY: 0, u: 0, v: 0, u2: 1, v2: 1, degrees: 0,
                           texture: {getImage: () => ({width: 1, height: 1})}};
    return attachment;
  };
}
const bytes = fs.readFileSync(skeletonPath);
const data = new spine.SkeletonBinary(loader).readSkeletonData(new Uint8Array(bytes));
if (!data.version.startsWith('3.8.')) throw new Error('Only the 3.8 format has been selected');
const result = {spineVersion: data.version, skeletonSha256: hash(bytes), runtimeSha256: hash(code),
  scope: 'serialized animation durations and events; not native animation update semantics',
  animations: data.animations.map(a => ({name: a.name, duration: a.duration,
    events: a.timelines.flatMap(t => t instanceof spine.EventTimeline
      ? t.events.map(e => ({time: e.time, name: e.data.name, intValue: e.intValue,
                           floatValue: e.floatValue, stringValue: e.stringValue})) : [])}))};
fs.writeFileSync(outputPath, JSON.stringify(result, null, 2));
console.log(JSON.stringify({spineVersion: data.version, animations: result.animations.length,
                           output: outputPath}));
