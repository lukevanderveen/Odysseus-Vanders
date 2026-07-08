// ============================================
// Council office background shader — raw WebGL, zero dependencies.
// A full-screen fragment shader rendered into one persistent <canvas> that we
// re-parent into the office canvas on each poll re-render (moving a live canvas
// keeps its GL context, so we never thrash the context). Idle = a slow
// horizontal drift; active (a report generating) = a radial pulse from the hub.
// Two looks via setMode: 'subtle' (theme-tinted, low-opacity underlay) and
// 'bold' (the original neon RGB full-bleed). The loop pauses when the office
// isn't visible — stop()/start() from the host, plus an internal
// visibilitychange guard — so it never burns GPU in the background.
//
// Not unit-tested: GLSL + WebGL can't run under Node. The only pure decision
// (mode toggle) lives in councilLogic.nextShaderMode, which is tested.
// ============================================

const VERT = `
  attribute vec2 position;
  void main() { gl_Position = vec4(position, 0.0, 1.0); }
`;

// uActive blends idle↔active; uMode blends subtle(0)↔bold(1); uTint colours the
// subtle look from the page's --fg so it respects the active theme. uTime is a
// frame-rate-independent clock (~1 unit ≈ 1/60 s), so speed is the same on a
// 60 Hz and a 144 Hz display.
const FRAG = `
  precision highp float;
  uniform vec2 uResolution;
  uniform float uTime;
  uniform float uActive;
  uniform float uMode;
  uniform vec3 uTint;

  void main(void) {
    vec2 uv = (gl_FragCoord.xy * 2.0 - uResolution.xy) / min(uResolution.x, uResolution.y);

    // Both modes are radial so the whole panel fills. Idle: the glow centre
    // drifts slowly left↔right (a gentle sweep). Active: it settles on the hub.
    float cx = sin(uTime * 0.004) * 1.4;
    vec2 center = mix(vec2(cx, 0.0), vec2(0.0), uActive);
    float coord = length(uv - center);

    float t = uTime * mix(0.0022, 0.006, uActive);
    float lineWidth = mix(0.0016, 0.0030, uActive);
    // A slow breath while idle, a stronger pulse while working.
    float pulse = 1.0 + mix(0.10, 0.45, uActive) * sin(uTime * mix(0.02, 0.05, uActive));

    vec3 color = vec3(0.0);
    for (int j = 0; j < 3; j++) {
      for (int i = 0; i < 5; i++) {
        color[j] += lineWidth * float(i * i)
          / abs(fract(t - 0.01 * float(j) + float(i) * 0.01) * 5.0 - coord + mod(uv.x + uv.y, 0.2));
      }
    }
    color *= pulse;

    // subtle: theme-tinted monochrome; bold: the original RGB split.
    float luma = dot(color, vec3(0.3333));
    vec3 finalColor = mix(luma * uTint * 1.6, color, uMode);
    gl_FragColor = vec4(finalColor, 1.0);
  }
`;

let _canvas = null;
let _gl = null;
let _program = null;
let _uniforms = null;
let _raf = 0;
let _time = 0;
let _last = 0;          // last frame timestamp (ms) for frame-rate-independent time
let _active = 0;          // smoothed 0→1 toward _activeTarget
let _activeTarget = 0;
let _mode = 0;            // smoothed 0→1 toward _modeTarget
let _modeTarget = 0;
let _tint = [0.6, 0.7, 1.0];
let _container = null;
let _running = false;     // host wants it rendering (office tab open)

const _compile = (gl, type, src) => {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    console.warn('council shader compile failed:', gl.getShaderInfoLog(sh));
    gl.deleteShader(sh);
    return null;
  }
  return sh;
};

// Lazily build the canvas + program once. Returns false if WebGL is unavailable.
const _ensure = () => {
  if (_canvas) return !!_gl;
  _canvas = document.createElement('canvas');
  _canvas.className = 'council-shader';
  _gl = _canvas.getContext('webgl') || _canvas.getContext('experimental-webgl');
  if (!_gl) return false;
  const gl = _gl;
  const vs = _compile(gl, gl.VERTEX_SHADER, VERT);
  const fs = _compile(gl, gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) { _gl = null; return false; }
  _program = gl.createProgram();
  gl.attachShader(_program, vs);
  gl.attachShader(_program, fs);
  gl.linkProgram(_program);
  gl.useProgram(_program);

  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(_program, 'position');
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

  _uniforms = {
    resolution: gl.getUniformLocation(_program, 'uResolution'),
    time: gl.getUniformLocation(_program, 'uTime'),
    active: gl.getUniformLocation(_program, 'uActive'),
    mode: gl.getUniformLocation(_program, 'uMode'),
    tint: gl.getUniformLocation(_program, 'uTint'),
  };
  return true;
};

// Pull the theme's foreground colour so the subtle look matches light/dark.
const _readTint = () => {
  if (!_container) return;
  try {
    const c = getComputedStyle(_container).getPropertyValue('--fg').trim()
      || getComputedStyle(document.body).color;
    const m = c.match(/(\d+(?:\.\d+)?)/g);
    if (m && m.length >= 3) _tint = [m[0] / 255, m[1] / 255, m[2] / 255];
  } catch (_) { /* keep the previous tint */ }
};

const _resize = () => {
  if (!_container || !_gl) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.max(1, Math.floor(_container.clientWidth * dpr));
  const h = Math.max(1, Math.floor(_container.clientHeight * dpr));
  if (_canvas.width !== w || _canvas.height !== h) {
    _canvas.width = w;
    _canvas.height = h;
    _gl.viewport(0, 0, w, h);
  }
};

const _frame = (now) => {
  if (!_running || document.hidden) { _raf = 0; _last = 0; return; }
  _raf = requestAnimationFrame(_frame);
  const gl = _gl;
  if (!gl) return;
  _resize();
  // Advance the clock by real elapsed time, normalised to 60 fps and capped so
  // a stutter or a backgrounded tab can't jump the animation.
  const ts = now || 0;
  const dt = _last ? Math.min((ts - _last) / 1000, 0.05) : 1 / 60;
  _last = ts;
  _time += dt * 60;
  _active += (_activeTarget - _active) * 0.04;   // ease toward the target
  _mode += (_modeTarget - _mode) * 0.08;
  gl.uniform2f(_uniforms.resolution, _canvas.width, _canvas.height);
  gl.uniform1f(_uniforms.time, _time);
  gl.uniform1f(_uniforms.active, _active);
  gl.uniform1f(_uniforms.mode, _mode);
  gl.uniform3f(_uniforms.tint, _tint[0], _tint[1], _tint[2]);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
};

const _startLoop = () => {
  if (!_raf && _running && !document.hidden) _raf = requestAnimationFrame(_frame);
};

// Attach (or re-attach) the shader into a freshly-rendered office canvas and
// run it. opts: { active: bool, mode: 'subtle'|'bold' }.
export const attach = (container, opts = {}) => {
  if (!container || !_ensure()) return;
  _container = container;
  if (_canvas.parentNode !== container) container.prepend(_canvas);
  _readTint();
  setActive(!!opts.active);
  if (opts.mode) setMode(opts.mode);
  _running = true;
  _resize();
  _startLoop();
};

export const setActive = (active) => { _activeTarget = active ? 1 : 0; };

export const setMode = (mode) => { _modeTarget = mode === 'bold' ? 1 : 0; };

// Stop rendering (office tab left / modal closed). Keeps the GL context warm.
export const stop = () => {
  _running = false;
  if (_raf) { cancelAnimationFrame(_raf); _raf = 0; }
};

// Resume the loop when the page becomes visible again, if the host still wants it.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) _startLoop();
});

export default { attach, setActive, setMode, stop };
