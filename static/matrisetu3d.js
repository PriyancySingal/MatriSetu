/* MatriSetu 3D theme v2 - "Setu" scene: a village and a hospital joined by a bridge, a heart above.
   Visual layer only; touches no page logic. */
(function () {
  var root = document.documentElement;
  var MODE = root.classList.contains('m3-landing') ? 'hero' : root.classList.contains('m3-login') ? 'login' : 'app';
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- card tilt (mouse devices only) ---------- */
  if (!reduce && matchMedia('(hover:hover) and (pointer:fine)').matches) {
    var SEL = '.stat-card,.stat,.kpi,.chart-card,.perf-card,.asha-perf-card,.scheme-card,.export-card,.child-card,.portal-card';
    document.addEventListener('pointermove', function (e) {
      var el = e.target.closest && e.target.closest(SEL); if (!el) return;
      var r = el.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
      el.classList.add('m3-tilt');
      el.style.transform = 'perspective(900px) rotateX(' + (-y * 6) + 'deg) rotateY(' + (x * 8) + 'deg) translateZ(6px)';
    });
    document.addEventListener('pointerout', function (e) {
      var el = e.target.closest && e.target.closest(SEL); if (el) el.style.transform = '';
    });
  }

  function boot() {
    var THREE = window.THREE; if (!THREE) return;
    var dark = MODE !== 'app';                       /* hero + login sit on deep green */
    var stage = document.createElement('div'), cv = document.createElement('canvas'); cv.id = 'm3-canvas'; stage.appendChild(cv);
    if (MODE === 'hero') {
      var hero = document.querySelector('.hero'); if (!hero) return;
      stage.className = 'm3-hero-stage'; hero.appendChild(stage);
    } else { stage.className = 'm3-bg-stage'; document.body.prepend(stage); }
    var ren;
    try { ren = new THREE.WebGLRenderer({ canvas: cv, antialias: true, alpha: true }); } catch (e) { stage.remove(); return; }
    ren.setPixelRatio(Math.min(devicePixelRatio, 1.5));
    var sc = new THREE.Scene(), cam = new THREE.PerspectiveCamera(38, 1, .1, 100);

    /* light */
    sc.add(new THREE.HemisphereLight(0xffffff, dark ? 0x3fbf98 : 0xbfe8d6, dark ? .62 : .7));
    var key = new THREE.DirectionalLight(0xffffff, dark ? .8 : .8); key.position.set(5, 9, 6); sc.add(key);
    var fill = new THREE.PointLight(0x7ddcbc, .8, 40); fill.position.set(-7, 3, 4); sc.add(fill);
    var warm = new THREE.PointLight(0xffb8a0, 1.0, 12); warm.position.set(0, 3.4, 1); sc.add(warm);

    var G = new THREE.Group(); sc.add(G);
    var M = function (c, o) { o = o || {}; o.color = c; o.roughness = o.roughness == null ? .55 : o.roughness; return new THREE.MeshStandardMaterial(o); };
    var add = function (m, x, y, z, p) { m.position.set(x, y, z); (p || G).add(m); return m; };
    var green = 0x12a37a, deep = 0x0a6b52, white = 0xffffff, coral = 0xff8a6b;

    /* island */
    add(new THREE.Mesh(new THREE.CylinderGeometry(5.5, 5.8, .4, 72), M(dark ? 0xd5f2e5 : 0xb7e6cf, { roughness: .8 })), 0, -.2, 0);
    var rim = add(new THREE.Mesh(new THREE.TorusGeometry(5.55, .06, 10, 120), M(green, { emissive: green, emissiveIntensity: .12 })), 0, .0, 0); rim.rotation.x = Math.PI / 2;
    var halo = add(new THREE.Mesh(new THREE.CircleGeometry(7.4, 64), new THREE.MeshBasicMaterial({ color: dark ? 0x7ddcbc : deep, transparent: true, opacity: dark ? .16 : .22, depthWrite: false })), 0, -.45, 0); halo.rotation.x = -Math.PI / 2;
    var grass = add(new THREE.Mesh(new THREE.CylinderGeometry(5.2, 5.2, .06, 72), M(dark ? 0x9fdcc0 : 0x7fcfa9, { roughness: 1 })), 0, .02, 0);

    /* village (left): little houses with terracotta roofs */
    [[-4.3, -1.2, 0], [-3.6, .5, .4], [-4.6, 1.3, -.3], [-3.5, -.4, -.5]].forEach(function (h, i) {
      var g = new THREE.Group(), s = .85 + (i % 2) * .2;
      add(new THREE.Mesh(new THREE.BoxGeometry(.7, .55, .7), M(white)), 0, .3, 0, g);
      var roof = add(new THREE.Mesh(new THREE.ConeGeometry(.62, .42, 4), M(coral)), 0, .78, 0, g); roof.rotation.y = Math.PI / 4;
      add(new THREE.Mesh(new THREE.BoxGeometry(.16, .28, .02), M(deep)), 0, .2, .36, g);
      g.scale.setScalar(s); g.position.set(h[0], 0, h[1]); g.rotation.y = h[2]; G.add(g);
    });
    /* hospital (right) */
    var hosp = new THREE.Group();
    add(new THREE.Mesh(new THREE.BoxGeometry(1.5, 1.1, 1.0), M(white)), 0, .55, 0, hosp);
    add(new THREE.Mesh(new THREE.BoxGeometry(.9, .7, .8), M(0xf0fbf6)), 1.0, .35, .05, hosp);
    add(new THREE.Mesh(new THREE.BoxGeometry(.46, .12, .06), M(green, { emissive: green, emissiveIntensity: .5 })), 0, .85, .52, hosp);
    add(new THREE.Mesh(new THREE.BoxGeometry(.12, .46, .06), M(green, { emissive: green, emissiveIntensity: .5 })), 0, .85, .52, hosp);
    hosp.position.set(3.9, 0, .2); G.add(hosp);
    /* trees */
    [[-2.2, 3.4], [2.0, 3.6], [-5.0, 2.8], [4.9, -2.2], [-1.2, -4.0], [2.6, -3.9], [5.0, 2.2], [-4.2, -3.0]].forEach(function (p, i) {
      var t = new THREE.Group(); add(new THREE.Mesh(new THREE.CylinderGeometry(.06, .08, .35, 8), M(0x8a6a4a)), 0, .18, 0, t);
      add(new THREE.Mesh(new THREE.ConeGeometry(.34, .8, 10), M(i % 2 ? green : deep)), 0, .72, 0, t);
      add(new THREE.Mesh(new THREE.ConeGeometry(.26, .6, 10), M(i % 2 ? 0x2fc79a : green)), 0, 1.12, 0, t);
      t.position.set(p[0], 0, p[1]); t.scale.setScalar(.8 + (i % 3) * .15); G.add(t);
    });

    /* the bridge ("setu"): tied arch, deck, hangers */
    var W = 2.7, ay = function (x) { return .3 + 1.55 * Math.sqrt(Math.max(0, 1 - x * x / (W * W))); }, pts = [];
    for (var i = 0; i <= 48; i++) { var x = -W + 2 * W * i / 48; pts.push(new THREE.Vector3(x, ay(x), 0)); }
    add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 96, .085, 10), M(white, { roughness: .3, emissive: 0xbdf0d9, emissiveIntensity: .25 })), 0, 0, .0);
    add(new THREE.Mesh(new THREE.BoxGeometry(2 * W + .4, .1, .6), M(deep)), 0, .26, 0);
    [-1.8, -1.2, -.6, 0, .6, 1.2, 1.8].forEach(function (x) {
      var h = ay(x) - .3; add(new THREE.Mesh(new THREE.CylinderGeometry(.018, .018, h, 6), M(0xd9f2e6)), x, .3 + h / 2, 0);
    });
    /* messages crossing the bridge: coral = alert, mint = resolved */
    var beads = [];
    for (var b = 0; b < 7; b++) {
      var c = b % 2 ? 0x35e0b0 : 0xff6b6b, m = new THREE.Mesh(new THREE.SphereGeometry(.11, 16, 16), new THREE.MeshBasicMaterial({ color: c }));
      G.add(m); beads.push({ m: m, o: b / 7, up: b % 2 === 0 });
    }

    /* heart (maternal care) with baby inside, above the bridge */
    var hs = new THREE.Shape();
    hs.moveTo(.25, .25); hs.bezierCurveTo(.25, .25, .2, 0, 0, 0); hs.bezierCurveTo(-.3, 0, -.3, .35, -.3, .35);
    hs.bezierCurveTo(-.3, .55, -.15, .77, .25, .95); hs.bezierCurveTo(.6, .77, .8, .55, .8, .35);
    hs.bezierCurveTo(.8, .35, .8, 0, .5, 0); hs.bezierCurveTo(.35, 0, .25, .25, .25, .25);
    var hg = new THREE.ExtrudeGeometry(hs, { depth: .35, bevelEnabled: true, bevelThickness: .12, bevelSize: .1, bevelSegments: 5, curveSegments: 28 });
    hg.rotateZ(Math.PI); hg.center(); hg.scale(2.1, 2.1, 2.1);
    var heartMat = M(dark ? 0x8ff0cf : 0x0c8a66, { transparent: true, opacity: dark ? .62 : .66, roughness: .25, metalness: .05, emissive: dark ? 0x2bbf92 : 0x0a6b52, emissiveIntensity: .25 });
    var heartG = new THREE.Group(); heartG.position.set(0, 3.9, 0); G.add(heartG);
    var heart = new THREE.Mesh(hg, heartMat); heart.renderOrder = 2; heartG.add(heart);
    var baby = new THREE.Mesh(new THREE.SphereGeometry(.34, 28, 28), M(0xffc4b0, { emissive: 0xff9a80, emissiveIntensity: .55, roughness: .4 })); baby.position.y = -.05; heartG.add(baby);
    var bhead = new THREE.Mesh(new THREE.SphereGeometry(.17, 20, 20), M(0xffd9cc, { emissive: 0xff9a80, emissiveIntensity: .4 })); bhead.position.set(.18, .22, .02); heartG.add(bhead);
    var ring = new THREE.Mesh(new THREE.TorusGeometry(1.75, .02, 8, 160), new THREE.MeshBasicMaterial({ color: dark ? 0xffffff : green, transparent: true, opacity: .55 })); heartG.add(ring);
    var ring2 = ring.clone(); ring2.scale.setScalar(1.22); heartG.add(ring2);
    var orbs = [0, 1, 2].map(function (i) { var o = new THREE.Mesh(new THREE.SphereGeometry(.09, 12, 12), new THREE.MeshBasicMaterial({ color: [0xff8a6b, 0x7ddcbc, 0xffffff][i] })); heartG.add(o); return o; });

    /* drifting leaves */
    var leaves = [];
    for (var k = 0; k < (innerWidth < 700 ? 10 : 18); k++) {
      var lm = new THREE.Mesh(new THREE.SphereGeometry(.2, 12, 8), M(k % 3 ? green : 0x5fd8ae, { roughness: .5 })); lm.scale.set(1, .22, .55);
      lm.userData = { a: Math.random() * 6.28, r: 6.3 + Math.random() * 2.6, y: .4 + Math.random() * 4.6, s: .04 + Math.random() * .06, p: Math.random() * 6 };
      G.add(lm); leaves.push(lm);
    }
    /* soft dust */
    var P = innerWidth < 700 ? 140 : 320, pos = new Float32Array(P * 3);
    for (var q = 0; q < P; q++) { var rr = 5 + Math.random() * 9, th = Math.random() * 6.283; pos.set([Math.cos(th) * rr, -1 + Math.random() * 8, Math.sin(th) * rr], q * 3); }
    var pg = new THREE.BufferGeometry(); pg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    var dust = new THREE.Points(pg, new THREE.PointsMaterial({ color: dark ? 0xffffff : green, size: .09, transparent: true, opacity: dark ? .7 : .45, depthWrite: false })); G.add(dust);

    /* camera framing */
    var look = new THREE.Vector3(0, 1.5, 0), shiftX = 0;
    function size() {
      var w, h;
      if (MODE === 'hero') { var r = stage.getBoundingClientRect(); w = r.width; h = r.height; } else { w = innerWidth; h = innerHeight; }
      if (!w || !h) return; ren.setSize(w, h, false); cam.aspect = w / h;
      var tv = Math.tan(cam.fov * Math.PI / 360), d = Math.max(7.2 / (tv * cam.aspect), 5.4 / tv) * 1.04;
      if (MODE === 'app') d *= 1.12; else if (MODE === 'hero') d *= .94;
      cam.position.set(0, 3.2 + d * .1, d); cam.lookAt(look); cam.updateProjectionMatrix();
      shiftX = MODE === 'login' && w > 900 ? 0 : 0;
      G.position.x = MODE === 'login' && w > 900 ? 0 : 0;
    }
    addEventListener('resize', size); size();
    if (window.ResizeObserver && MODE === 'hero') new ResizeObserver(size).observe(stage);

    /* interaction */
    var mx = 0, my = 0, sy = 0, drag = false, lx = 0, spin = 0, vel = 0, run = true, t0 = performance.now();
    addEventListener('pointermove', function (e) { mx = e.clientX / innerWidth - .5; my = e.clientY / innerHeight - .5; if (drag) { vel = (e.clientX - lx) * .006; spin += vel; lx = e.clientX; } });
    if (MODE === 'hero') { stage.addEventListener('pointerdown', function (e) { drag = true; lx = e.clientX; }); addEventListener('pointerup', function () { drag = false; }); }
    addEventListener('scroll', function () { sy = scrollY; }, { passive: true });
    document.addEventListener('visibilitychange', function () { run = !document.hidden; if (run) loop(performance.now()); });

    function beat(t) { var p = (t * 1.15) % 1; return Math.pow(Math.max(0, Math.sin(p * 6.283)), 6) + .55 * Math.pow(Math.max(0, Math.sin((p - .2) * 6.283)), 6); }
    function loop(now) {
      if (!run) return;
      var t = (now - t0) / 1000;
      if (MODE === 'hero' && stage.getBoundingClientRect().bottom < 0) { requestAnimationFrame(loop); return; }
      if (!drag) vel *= .94;
      G.rotation.y = reduce ? .5 : t * .16 + spin + (MODE === 'app' ? sy * .0009 : 0);
      G.rotation.x = .02 + (-my) * .10; G.rotation.z = mx * .04;
      heartG.rotation.y = -G.rotation.y + Math.sin(t * .6) * .45;  /* heart keeps facing the viewer while the island revolves */
      var s = 1 + .07 * beat(t); heartG.scale.setScalar(s); heartG.position.y = 3.9 + Math.sin(t * 1.1) * .12; 
      baby.scale.setScalar(.92 + .12 * beat(t)); warm.intensity = .9 + .5 * beat(t);
      ring.rotation.set(Math.PI / 2.3, 0, t * .6); ring2.rotation.set(Math.PI / 1.7, t * .4, 0);
      orbs.forEach(function (o, i) { var a = t * (.9 + i * .3) + i * 2.1; o.position.set(Math.cos(a) * 1.75, Math.sin(a * 1.2) * .5, Math.sin(a) * 1.75); });
      beads.forEach(function (b) { var u = (t * .11 + b.o) % 1, x = (b.up ? -W + 2 * W * u : W - 2 * W * u); b.m.position.set(x, .42 + Math.sin(u * 6.283 * 2) * .05, 0); });
      leaves.forEach(function (l) { var d = l.userData, a = d.a + t * d.s; l.position.set(Math.cos(a) * d.r, d.y + Math.sin(t * .8 + d.p) * .35, Math.sin(a) * d.r); l.rotation.set(t * .5 + d.p, t * .3, t * .4); });
      dust.rotation.y = t * .02;
      ren.render(sc, cam);
      if (!reduce) requestAnimationFrame(loop);
    }
    loop(performance.now()); requestAnimationFrame(function () { cv.classList.add('on'); });
  }

  var s = document.createElement('script');
  s.src = 'https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js';
  s.onload = function () { if (document.body) boot(); else addEventListener('DOMContentLoaded', boot); };
  document.head.appendChild(s);
})();
