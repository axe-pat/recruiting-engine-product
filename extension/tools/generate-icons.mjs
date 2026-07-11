import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const iconsDir = resolve(here, "../icons");
const sizes = [16, 32, 48, 128];
const supersample = 4;

const colors = {
  ink: [17, 19, 15, 255],
  signal: [216, 255, 95, 255],
  coral: [255, 105, 79, 255],
  transparent: [0, 0, 0, 0],
};

mkdirSync(iconsDir, { recursive: true });
for (const size of sizes) {
  writeFileSync(resolve(iconsDir, `icon-${size}.png`), encodePng(size, render(size)));
}

function render(size) {
  const output = Buffer.alloc(size * size * 4);
  const samples = supersample * supersample;
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      let opaque = 0;
      let red = 0;
      let green = 0;
      let blue = 0;
      for (let sy = 0; sy < supersample; sy += 1) {
        for (let sx = 0; sx < supersample; sx += 1) {
          const nx = (x + (sx + 0.5) / supersample) / size;
          const ny = (y + (sy + 0.5) / supersample) / size;
          const color = sample(nx, ny);
          if (color[3] > 0) {
            opaque += 1;
            red += color[0];
            green += color[1];
            blue += color[2];
          }
        }
      }
      const offset = (y * size + x) * 4;
      if (opaque === 0) {
        output.set(colors.transparent, offset);
      } else {
        output[offset] = Math.round(red / opaque);
        output[offset + 1] = Math.round(green / opaque);
        output[offset + 2] = Math.round(blue / opaque);
        output[offset + 3] = Math.round((opaque / samples) * 255);
      }
    }
  }
  return output;
}

function sample(x, y) {
  const radius = distance(x, y, 0.5, 0.5);
  if (radius > 0.46) return colors.transparent;
  let color = radius > 0.425 ? colors.ink : colors.signal;

  const stroke = 0.034;
  const rPath =
    onSegment(x, y, 0.36, 0.29, 0.36, 0.71, stroke) ||
    onSegment(x, y, 0.36, 0.29, 0.55, 0.29, stroke) ||
    onSegment(x, y, 0.36, 0.50, 0.55, 0.50, stroke) ||
    onSegment(x, y, 0.55, 0.29, 0.64, 0.36, stroke) ||
    onSegment(x, y, 0.64, 0.36, 0.55, 0.50, stroke) ||
    onSegment(x, y, 0.53, 0.50, 0.68, 0.69, stroke);
  if (rPath) color = colors.ink;

  const endpoint = distance(x, y, 0.71, 0.72);
  if (endpoint < 0.083) color = colors.ink;
  if (endpoint < 0.058) color = colors.coral;
  return color;
}

function onSegment(px, py, x1, y1, x2, y2, thickness) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const denominator = dx * dx + dy * dy;
  const projection = denominator === 0 ? 0 : ((px - x1) * dx + (py - y1) * dy) / denominator;
  const t = Math.max(0, Math.min(1, projection));
  return distance(px, py, x1 + t * dx, y1 + t * dy) <= thickness;
}

function distance(x1, y1, x2 = 0, y2 = 0) {
  return Math.hypot(x1 - x2, y1 - y2);
}

function encodePng(size, rgba) {
  const scanlines = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y += 1) {
    const rowOffset = y * (size * 4 + 1);
    scanlines[rowOffset] = 0;
    rgba.copy(scanlines, rowOffset + 1, y * size * 4, (y + 1) * size * 4);
  }

  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8;
  header[9] = 6;
  header[10] = 0;
  header[11] = 0;
  header[12] = 0;

  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(scanlines, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function chunk(type, data) {
  const name = Buffer.from(type, "ascii");
  const body = Buffer.concat([name, data]);
  const result = Buffer.alloc(12 + data.length);
  result.writeUInt32BE(data.length, 0);
  body.copy(result, 4);
  result.writeUInt32BE(crc32(body), 8 + data.length);
  return result;
}

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}
