'use client';

import React, { useEffect, useRef, useState } from 'react';
import type { RadarPoint } from './types';
import { UNCLUSTERED_ID } from './types';

/** A token pinned above its narrative. Only resolved tokens older than 7 days are ever passed in. */
export interface RadarPin {
  clusterId: string;
  label: string;
  reached: boolean;
}

interface Props {
  points: RadarPoint[];
  pins?: RadarPin[];
  className?: string;
}

const MAX_BLIPS = 220;
const SWEEP_SECONDS = 7;
const TRAIL = 1.0; // radians of fan behind the sweep edge
const TAU = Math.PI * 2;
const AMBER = 0xffb23e;

/** Fixed-size sample, evenly spaced through the list, so every narrative keeps its share of blips. */
function sample<T>(list: T[], n: number): T[] {
  if (list.length <= n) return list;
  const step = list.length / n;
  return Array.from({ length: n }, (_, i) => list[Math.floor(i * step)]);
}

/**
 * Anonymous points into the unit disc. Each axis blends linear and rank position (so tight groups spread out
 * and gaps close), then groups are pulled towards the middle, as on the flat map. Qualitative, not to scale.
 */
function toDisc(points: RadarPoint[]): { x: number; z: number; p: RadarPoint }[] {
  const BLEND = 0.85;
  const GAP = 0.7;
  const axis = (v: number[]): number[] => {
    const n = v.length;
    const order = v.map((a, i) => [a, i] as const).sort((a, b) => a[0] - b[0]);
    const rank = new Array<number>(n);
    order.forEach(([, i], r) => { rank[i] = n > 1 ? r / (n - 1) : 0.5; });
    const lo = order[Math.floor((n - 1) * 0.02)][0];
    const hi = order[Math.ceil((n - 1) * 0.98)][0];
    return v.map((a, i) => (1 - BLEND) * Math.min(Math.max((a - lo) / (hi - lo || 1), 0), 1) + BLEND * rank[i]);
  };
  const xs = axis(points.map((p) => p.x));
  const ys = axis(points.map((p) => p.y));
  const sums = new Map<string, { x: number; y: number; n: number }>();
  points.forEach((p, i) => {
    const s = sums.get(p.cluster_id) ?? { x: 0, y: 0, n: 0 };
    s.x += xs[i];
    s.y += ys[i];
    s.n += 1;
    sums.set(p.cluster_id, s);
  });
  return points.map((p, i) => {
    const s = sums.get(p.cluster_id)!;
    const cx = s.x / s.n;
    const cy = s.y / s.n;
    let x = ((0.5 + (cx - 0.5) * GAP + (xs[i] - cx) * 1.7) * 2 - 1) * 0.9;
    let z = ((0.5 + (cy - 0.5) * GAP + (ys[i] - cy) * 1.7) * 2 - 1) * 0.9;
    const r = Math.hypot(x, z);
    if (r > 0.94) {
      x = (x / r) * 0.94;
      z = (z / r) * 0.94;
    }
    return { x, z, p };
  });
}

const RadarFallback: React.FC<{ className?: string }> = ({ className }) => (
  <svg viewBox="-110 -110 220 220" className={className} role="img" aria-label="Radar graphic">
    {[100, 75, 50, 25].map((r) => <circle key={r} r={r} fill="none" stroke="var(--banana)" strokeOpacity={r === 100 ? 0.4 : 0.18} />)}
    <line x1="-100" x2="100" y1="0" y2="0" stroke="var(--banana)" strokeOpacity="0.15" />
    <line y1="-100" y2="100" x1="0" x2="0" stroke="var(--banana)" strokeOpacity="0.15" />
  </svg>
);

/**
 * WebGL radar (three.js, loaded on demand), a HUD in brand amber: segmented outer rings with a tick scale, a dot
 * matrix, range rings, a hard-edged sweep fan and one blip per token (anonymous, all amber: gold reached $30K,
 * amber stalled, deep amber still pending; a taller stem also marks $30K). Pins float above a few older, resolved tokens. Everything lights as the
 * sweep passes. Falls back to a static SVG without WebGL and holds still under prefers-reduced-motion.
 */
export const Radar3D: React.FC<Props> = ({ points, pins = [], className }) => {
  const mountRef = useRef<HTMLDivElement>(null);
  const [fallback, setFallback] = useState(false);
  const pinKey = pins.map((p) => `${p.clusterId}:${p.label}:${p.reached}`).join('|');

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || points.length === 0) return;
    let disposed = false;
    let cleanup = () => {};

    (async () => {
      const THREE = await import('three');
      if (disposed) return;

      let renderer: InstanceType<typeof THREE.WebGLRenderer>;
      try {
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
      } catch {
        setFallback(true);
        return;
      }
      const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setClearColor(0x000000, 0);
      mount.appendChild(renderer.domElement);
      renderer.domElement.style.cssText = 'display:block;width:100%;height:100%';

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 30);
      const root = new THREE.Group();
      scene.add(root);
      const disposables: { dispose: () => void }[] = [];
      const track = <T extends { dispose: () => void }>(o: T): T => {
        disposables.push(o);
        return o;
      };
      const additive = { transparent: true, depthWrite: false, blending: THREE.AdditiveBlending } as const;
      const amber = new THREE.Color(AMBER);

      // Flat ring or arc on the disc plane, lit additively. Thin rings plus a wider faint one give the glow.
      const ring = (r0: number, r1: number, opacity: number, from = 0, len = TAU, segs = 128) => {
        const geo = track(new THREE.RingGeometry(r0, r1, segs, 1, from, len));
        const mat = track(new THREE.MeshBasicMaterial({ color: AMBER, opacity, side: THREE.DoubleSide, ...additive }));
        const m = new THREE.Mesh(geo, mat);
        m.rotation.x = -Math.PI / 2;
        root.add(m);
        return m;
      };

      // Disc body: dark glass with a warm centre
      const tex = document.createElement('canvas');
      tex.width = tex.height = 256;
      const g = tex.getContext('2d')!;
      const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128);
      grad.addColorStop(0, 'rgba(255,176,60,0.20)');
      grad.addColorStop(0.55, 'rgba(60,34,8,0.40)');
      grad.addColorStop(1, 'rgba(14,8,3,0.70)');
      g.fillStyle = grad;
      g.fillRect(0, 0, 256, 256);
      const discMat = track(new THREE.MeshBasicMaterial({ map: track(new THREE.CanvasTexture(tex)), transparent: true }));
      const disc = new THREE.Mesh(track(new THREE.CircleGeometry(1, 96)), discMat);
      disc.rotation.x = -Math.PI / 2;
      root.add(disc);

      // Range rings, crosshair, outer rim with its glow
      [0.25, 0.5, 0.75].forEach((r) => ring(r - 0.003, r + 0.003, 0.4));
      ring(0.992, 1.008, 0.95);
      ring(0.96, 1.04, 0.12);
      const spokePts: InstanceType<typeof THREE.Vector3>[] = [];
      for (let k = 0; k < 12; k++) {
        const a = (k / 12) * TAU;
        spokePts.push(new THREE.Vector3(0, 0.002, 0), new THREE.Vector3(Math.cos(a), 0.002, Math.sin(a)));
      }
      root.add(new THREE.LineSegments(track(new THREE.BufferGeometry().setFromPoints(spokePts)),
        track(new THREE.LineBasicMaterial({ color: AMBER, ...additive, opacity: 0.16 }))));

      // HUD outside the disc: broken bright arcs, a thin dotted track and a tick scale
      const arcs: [number, number][] = [[0.1, 0.55], [0.8, 0.35], [1.35, 0.5], [2.1, 0.28], [2.55, 0.6], [3.4, 0.3], [3.85, 0.7], [4.7, 0.32], [5.2, 0.45], [5.85, 0.3]];
      arcs.forEach(([from, len]) => ring(1.1, 1.145, 0.85, from, len, 24));
      ring(1.19, 1.195, 0.35);
      const tickPts: InstanceType<typeof THREE.Vector3>[] = [];
      for (let k = 0; k < 120; k++) {
        const a = (k / 120) * TAU;
        const long = k % 10 === 0;
        const r0 = 1.22;
        const r1 = long ? 1.31 : 1.26;
        tickPts.push(new THREE.Vector3(Math.cos(a) * r0, 0.002, Math.sin(a) * r0), new THREE.Vector3(Math.cos(a) * r1, 0.002, Math.sin(a) * r1));
      }
      root.add(new THREE.LineSegments(track(new THREE.BufferGeometry().setFromPoints(tickPts)),
        track(new THREE.LineBasicMaterial({ color: AMBER, ...additive, opacity: 0.5 }))));

      // Dot matrix across the disc, brightened by the sweep
      const dots: { x: number; z: number; a: number }[] = [];
      for (let x = -0.97; x <= 0.97; x += 0.052) {
        for (let z = -0.97; z <= 0.97; z += 0.052) {
          if (Math.hypot(x, z) < 0.97) dots.push({ x, z, a: Math.atan2(-z, x) });
        }
      }
      const dotPos = new Float32Array(dots.length * 3);
      dots.forEach((d, i) => dotPos.set([d.x, 0.003, d.z], i * 3));
      const dotCol = new Float32Array(dots.length * 3);
      const dotGeo = track(new THREE.BufferGeometry());
      dotGeo.setAttribute('position', new THREE.BufferAttribute(dotPos, 3));
      dotGeo.setAttribute('color', new THREE.BufferAttribute(dotCol, 3));
      root.add(new THREE.Points(dotGeo, track(new THREE.PointsMaterial({ size: 0.012, vertexColors: true, sizeAttenuation: true, ...additive }))));

      // Sweep: a fan with hard edges, brightest at the leading edge
      const sweepMat = track(new THREE.ShaderMaterial({
        transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
        uniforms: { uAngle: { value: 0 }, uTrail: { value: TRAIL } },
        vertexShader: 'varying vec2 vP; void main(){ vP = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
        fragmentShader: `
          varying vec2 vP; uniform float uAngle; uniform float uTrail;
          void main(){
            float ang = atan(vP.y, vP.x);
            float d = mod(uAngle - ang, 6.2831853);
            float inFan = step(d, uTrail);
            float fan = inFan * (0.06 + 0.36 * pow(1.0 - d / uTrail, 1.4));
            float lead = step(d, 0.012) * 0.95;
            float tail = step(abs(d - uTrail), 0.008) * 0.4;
            float r = length(vP);
            float a = (fan + lead + tail) * (1.0 - smoothstep(0.985, 1.0, r)) * (0.35 + 0.65 * smoothstep(0.0, 0.5, r));
            gl_FragColor = vec4(1.0, 0.72, 0.28, a);
          }`,
      }));
      const sweep = new THREE.Mesh(track(new THREE.CircleGeometry(1, 128)), sweepMat);
      sweep.rotation.x = -Math.PI / 2;
      sweep.position.y = 0.004;
      root.add(sweep);

      // Centre hotspot
      const hot = document.createElement('canvas');
      hot.width = hot.height = 128;
      const hg = hot.getContext('2d')!;
      const hgrad = hg.createRadialGradient(64, 64, 0, 64, 64, 64);
      hgrad.addColorStop(0, 'rgba(255,240,200,1)');
      hgrad.addColorStop(0.18, 'rgba(255,190,80,0.85)');
      hgrad.addColorStop(1, 'rgba(255,150,30,0)');
      hg.fillStyle = hgrad;
      hg.fillRect(0, 0, 128, 128);
      const hotMat = track(new THREE.SpriteMaterial({ map: track(new THREE.CanvasTexture(hot)), ...additive }));
      const hotspot = new THREE.Sprite(hotMat);
      hotspot.scale.setScalar(0.22);
      hotspot.position.y = 0.01;
      root.add(hotspot);

      // Blips and stems
      const items = toDisc(sample(points, MAX_BLIPS));
      const n = items.length;
      const blips = new THREE.InstancedMesh(track(new THREE.SphereGeometry(0.02, 14, 14)), track(new THREE.MeshBasicMaterial({ color: 0xffffff })), n);
      blips.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3);
      root.add(blips);
      const halos = new THREE.InstancedMesh(
        track(new THREE.SphereGeometry(0.042, 12, 12)),
        track(new THREE.MeshBasicMaterial({ color: 0xffffff, opacity: 0.16, ...additive })), n);
      halos.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3);
      root.add(halos);

      const base: InstanceType<typeof THREE.Color>[] = [];
      const heights: number[] = [];
      const angles: number[] = [];
      const stemPos = new Float32Array(n * 6);
      items.forEach(({ x, z, p }, i) => {
        // One palette, the site's amber: gold reached $30K, amber stalled, deep amber still inside 48h
        const un = p.cluster_id === UNCLUSTERED_ID;
        base.push(new THREE.Color(p.reached_30k ? 0xffd36b : !p.resolved || un ? 0xb9781f : 0xf0a431));
        const height = p.reached_30k ? 0.2 : p.resolved ? 0.06 : 0.03;
        heights.push(height);
        angles.push(Math.atan2(-z, x));
        stemPos.set([x, 0.004, z, x, height, z], i * 6);
      });
      const stemGeo = track(new THREE.BufferGeometry());
      stemGeo.setAttribute('position', new THREE.BufferAttribute(stemPos, 3));
      root.add(new THREE.LineSegments(stemGeo, track(new THREE.LineBasicMaterial({ color: AMBER, ...additive, opacity: 0.4 }))));

      // Pins: a lens-shaped marker with the token symbol, floating above the middle of its narrative
      const centres = new Map<string, { x: number; z: number; n: number }>();
      items.forEach(({ x, z, p }) => {
        const c = centres.get(p.cluster_id) ?? { x: 0, z: 0, n: 0 };
        c.x += x;
        c.z += z;
        c.n += 1;
        centres.set(p.cluster_id, c);
      });
      const used = new Map<string, number>();
      const pinObjs = pins.flatMap((pin) => {
        const c = centres.get(pin.clusterId);
        if (!c) return [];
        const k = used.get(pin.clusterId) ?? 0;
        used.set(pin.clusterId, k + 1);
        const a = k * 2.4;
        const x = c.x / c.n + Math.cos(a) * 0.09 * k;
        const z = c.z / c.n + Math.sin(a) * 0.09 * k;
        const cv = document.createElement('canvas');
        cv.width = cv.height = 160;
        const cx = cv.getContext('2d')!;
        const col = pin.reached ? '#ffd27a' : '#e99f30';
        cx.shadowColor = col;
        cx.shadowBlur = 14;
        cx.fillStyle = 'rgba(30,18,6,0.92)';
        cx.strokeStyle = col;
        cx.lineWidth = 5;
        cx.beginPath();
        cx.arc(80, 62, 44, 0, TAU);
        cx.fill();
        cx.stroke();
        cx.beginPath();
        cx.moveTo(66, 100);
        cx.lineTo(80, 134);
        cx.lineTo(94, 100);
        cx.stroke();
        cx.shadowBlur = 0;
        cx.fillStyle = col;
        cx.font = `700 ${pin.label.length > 3 ? 26 : 32}px ui-monospace, Menlo, monospace`;
        cx.textAlign = 'center';
        cx.textBaseline = 'middle';
        cx.fillText(pin.label.slice(0, 4).toUpperCase(), 80, 63);
        const mat = track(new THREE.SpriteMaterial({ map: track(new THREE.CanvasTexture(cv)), transparent: true, depthWrite: false }));
        const sprite = new THREE.Sprite(mat);
        sprite.center.set(0.5, 0.16);
        sprite.scale.setScalar(0.26);
        sprite.position.set(x, 0.3, z);
        root.add(sprite);
        const stem = new THREE.Line(
          track(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(x, 0.004, z), new THREE.Vector3(x, 0.3, z)])),
          track(new THREE.LineBasicMaterial({ color: AMBER, ...additive, opacity: 0.55 })));
        root.add(stem);
        return [{ sprite, mat, angle: Math.atan2(-z, x) }];
      });

      const dummy = new THREE.Object3D();
      const tmp = new THREE.Color();
      const glowAt = (angle: number, sweepAngle: number, span: number) => {
        const d = (((sweepAngle - angle) % TAU) + TAU) % TAU;
        return d < span ? Math.exp((-d * 2.2) / span * 1.2) : 0;
      };
      const paint = (sweepAngle: number, t: number) => {
        for (let i = 0; i < n; i++) {
          const glow = glowAt(angles[i], sweepAngle, TRAIL * 1.6);
          dummy.position.set(items[i].x, heights[i], items[i].z);
          dummy.scale.setScalar(1 + 1.1 * glow);
          dummy.updateMatrix();
          blips.setMatrixAt(i, dummy.matrix);
          tmp.copy(base[i]).multiplyScalar(1 + 0.25 * glow);
          blips.setColorAt(i, tmp);
          dummy.scale.setScalar(1 + 1.6 * glow);
          dummy.updateMatrix();
          halos.setMatrixAt(i, dummy.matrix);
          tmp.copy(base[i]).multiplyScalar(glow);
          halos.setColorAt(i, tmp);
        }
        blips.instanceMatrix.needsUpdate = true;
        halos.instanceMatrix.needsUpdate = true;
        if (blips.instanceColor) blips.instanceColor.needsUpdate = true;
        if (halos.instanceColor) halos.instanceColor.needsUpdate = true;

        for (let i = 0; i < dots.length; i++) {
          const f = 0.13 + 0.9 * glowAt(dots[i].a, sweepAngle, TRAIL * 1.3);
          dotCol[i * 3] = amber.r * f;
          dotCol[i * 3 + 1] = amber.g * f;
          dotCol[i * 3 + 2] = amber.b * f;
        }
        dotGeo.attributes.color.needsUpdate = true;

        pinObjs.forEach((p) => {
          const glow = glowAt(p.angle, sweepAngle, TRAIL * 1.6);
          p.sprite.scale.setScalar(0.26 * (1 + 0.22 * glow));
          p.mat.opacity = 0.78 + 0.22 * glow;
        });
        hotspot.scale.setScalar(0.2 + 0.03 * Math.sin(t * 3));
        sweepMat.uniforms.uAngle.value = sweepAngle;
      };

      // View: above the disc, slow drift, tilt towards the pointer
      let tiltX = 0;
      let tiltY = 0;
      let targetX = 0;
      let targetY = 0;
      const onMove = (e: PointerEvent) => {
        const r = mount.getBoundingClientRect();
        targetY = ((e.clientX - r.left) / r.width - 0.5) * 0.5;
        targetX = ((e.clientY - r.top) / r.height - 0.5) * 0.22;
      };
      const onLeave = () => { targetX = 0; targetY = 0; };
      mount.addEventListener('pointermove', onMove);
      mount.addEventListener('pointerleave', onLeave);

      const resize = () => {
        const w = mount.clientWidth || 1;
        const h = mount.clientHeight || 1;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        const wide = w / h > 1.2;
        camera.position.set(0, wide ? 2.1 : 2.4, wide ? 3.1 : 3.7);
        camera.lookAt(0, 0.02, 0);
        camera.updateProjectionMatrix();
      };
      const ro = new ResizeObserver(() => { resize(); if (reduceMotion) { paint(0.9, 0); renderer.render(scene, camera); } });
      ro.observe(mount);
      resize();

      let raf = 0;
      let visible = true;
      const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; });
      io.observe(mount);
      const t0 = performance.now();
      const frame = (now: number) => {
        raf = requestAnimationFrame(frame);
        if (!visible) return;
        const t = (now - t0) / 1000;
        tiltX += (targetX - tiltX) * 0.06;
        tiltY += (targetY - tiltY) * 0.06;
        root.rotation.x = tiltX;
        root.rotation.y = tiltY + Math.sin(t * 0.15) * 0.06;
        paint(((t / SWEEP_SECONDS) * TAU) % TAU, t);
        renderer.render(scene, camera);
      };
      if (reduceMotion) {
        paint(0.9, 0);
        renderer.render(scene, camera);
      } else {
        raf = requestAnimationFrame(frame);
      }

      cleanup = () => {
        cancelAnimationFrame(raf);
        ro.disconnect();
        io.disconnect();
        mount.removeEventListener('pointermove', onMove);
        mount.removeEventListener('pointerleave', onLeave);
        disposables.forEach((d) => d.dispose());
        blips.dispose();
        halos.dispose();
        renderer.dispose();
        renderer.domElement.remove();
      };
    })();

    return () => {
      disposed = true;
      cleanup();
    };
    // pinKey stands for the pins' content, so the scene is rebuilt only when they really change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, pinKey]);

  if (fallback) return <RadarFallback className={className} />;
  return <div ref={mountRef} className={className} aria-hidden />;
};
