/* maijev intro page — ambient Three.js particle pipeline, light theme.
 * Classic script (THREE global from r128 UMD) so file:// works directly.
 * Scroll progress drives camera along the curve and lights stage nodes.
 */
(function () {
  var canvas = document.getElementById("bg3d");
  var renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(55, 1, 0.1, 200);

  var STAGES = [
    "视频/URL", "ffmpeg", "MAI ASR", "atoms",
    "merge", "pre-pass", "translate", "SRT"
  ];
  var pts = [];
  for (var i = 0; i < STAGES.length; i++) {
    pts.push(new THREE.Vector3(
      -21 + i * 6,
      Math.sin(i * 1.1) * 1.6,
      Math.cos(i * 0.8) * 2.4
    ));
  }
  var curve = new THREE.CatmullRomCurve3(pts);

  var rail = new THREE.Mesh(
    new THREE.TubeGeometry(curve, 200, 0.03, 8, false),
    new THREE.MeshBasicMaterial({
      color: 0xc7d6ee, transparent: true, opacity: 0.8
    })
  );
  scene.add(rail);

  function textSprite(text) {
    var c = document.createElement("canvas");
    c.width = 512; c.height = 128;
    var g = c.getContext("2d");
    g.font = "600 42px Inter, -apple-system, PingFang SC, sans-serif";
    g.textAlign = "center"; g.textBaseline = "middle";
    g.fillStyle = "rgba(71,85,105,.9)";
    g.fillText(text, 256, 64);
    var m = new THREE.SpriteMaterial({
      map: new THREE.CanvasTexture(c),
      transparent: true, depthWrite: false
    });
    var s = new THREE.Sprite(m);
    s.scale.set(4.4, 1.1, 1);
    return s;
  }

  function glowSprite(color, scale) {
    var c = document.createElement("canvas");
    c.width = c.height = 128;
    var g = c.getContext("2d");
    var grad = g.createRadialGradient(64, 64, 4, 64, 64, 62);
    grad.addColorStop(0, color);
    grad.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 128, 128);
    var m = new THREE.SpriteMaterial({
      map: new THREE.CanvasTexture(c),
      transparent: true, depthWrite: false, opacity: 0.5
    });
    var s = new THREE.Sprite(m);
    s.scale.set(scale, scale, 1);
    return s;
  }

  var nodes = [];
  pts.forEach(function (p, i) {
    var grp = new THREE.Group();
    grp.position.copy(p);
    var core = new THREE.Mesh(
      new THREE.SphereGeometry(0.3, 24, 24),
      new THREE.MeshBasicMaterial({ color: 0x3b82f6 })
    );
    var halo = glowSprite("rgba(59,130,246,0.55)", 2.6);
    var label = textSprite(STAGES[i]);
    label.position.y = 1.15;
    grp.add(core); grp.add(halo); grp.add(label);
    grp.userData = { core: core, halo: halo, label: label, i: i };
    scene.add(grp);
    nodes.push(grp);
  });

  var N = 2200;
  var pos = new Float32Array(N * 3);
  var col = new Float32Array(N * 3);
  var tOff = new Float32Array(N);
  var speed = new Float32Array(N);
  var jitter = new Float32Array(N * 3);
  var cA = new THREE.Color(0x3b82f6), cB = new THREE.Color(0x8b5cf6);
  for (var j = 0; j < N; j++) {
    tOff[j] = Math.random();
    speed[j] = 0.02 + Math.random() * 0.05;
    for (var k = 0; k < 3; k++) jitter[j * 3 + k] = (Math.random() - 0.5) * 0.65;
    var c = cA.clone().lerp(cB, Math.random());
    col[j * 3] = c.r; col[j * 3 + 1] = c.g; col[j * 3 + 2] = c.b;
  }
  var geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  var particles = new THREE.Points(geo, new THREE.PointsMaterial({
    size: 0.085, vertexColors: true, transparent: true, opacity: 0.55,
    depthWrite: false
  }));
  scene.add(particles);

  var SN = 350, sp = new Float32Array(SN * 3);
  for (var s = 0; s < SN * 3; s++) sp[s] = (Math.random() - 0.5) * 90;
  var sg = new THREE.BufferGeometry();
  sg.setAttribute("position", new THREE.BufferAttribute(sp, 3));
  scene.add(new THREE.Points(sg, new THREE.PointsMaterial({
    size: 0.05, color: 0xb8c4dd, transparent: true, opacity: 0.55
  })));

  function resize() {
    var w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  resize();

  var clock = new THREE.Clock();
  var v3 = new THREE.Vector3();

  function animate() {
    requestAnimationFrame(animate);
    var time = clock.getElapsedTime();

    var doc = document.documentElement;
    var progress = doc.scrollHeight > window.innerHeight
      ? window.scrollY / (doc.scrollHeight - window.innerHeight) : 0;

    var camT = Math.min(progress * 1.02, 0.98);
    curve.getPointAt(camT, v3);
    camera.position.set(
      v3.x + Math.sin(time * 0.25) * 0.6,
      v3.y + 2.6 + Math.sin(time * 0.4) * 0.25,
      v3.z + 7.5
    );
    var lookT = Math.min(camT + 0.06, 1);
    curve.getPointAt(lookT, v3);
    camera.lookAt(v3.x, v3.y, v3.z);

    var active = Math.min(
      STAGES.length - 1, Math.floor(progress * (STAGES.length + 1)) - 1
    );
    nodes.forEach(function (grp, i) {
      var on = i === Math.max(0, active);
      var pulse = on ? 1 + Math.sin(time * 5) * 0.22 : 1;
      var target = on ? 1.7 * pulse : 1;
      grp.userData.core.scale.setScalar(
        grp.userData.core.scale.x + (target - grp.userData.core.scale.x) * 0.12
      );
      grp.userData.halo.material.opacity = on ? 0.85 : 0.4;
      grp.userData.label.material.opacity = on ? 1 : 0.5;
    });

    for (var p = 0; p < N; p++) {
      var t = (tOff[p] + time * speed[p]) % 1;
      curve.getPointAt(t, v3);
      pos[p * 3] = v3.x + jitter[p * 3];
      pos[p * 3 + 1] = v3.y + jitter[p * 3 + 1];
      pos[p * 3 + 2] = v3.z + jitter[p * 3 + 2];
    }
    geo.attributes.position.needsUpdate = true;
    renderer.render(scene, camera);
  }
  animate();
})();
