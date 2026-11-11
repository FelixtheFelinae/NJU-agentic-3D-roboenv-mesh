import React, { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { PLYLoader } from "three/addons/loaders/PLYLoader.js";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { RotateCcw, Grid2X2, Axis3D, Scan, Box } from "lucide-react";

export function ModelViewer({ url, name, type, objects, selectedPart }) {
  const identity = JSON.stringify(objects ?? [{url,name,type}]);
  const host = useRef(null),
    state = useRef(null);
  const [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  const [wire, setWire] = useState(false),
    [axes, setAxes] = useState(true);
  useEffect(() => {
    if (!host.current) return;
    setError("");
    setLoading(true);
    setWire(false);
    const container = host.current;
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      setError("当前浏览器无法创建 WebGL 视图，请查看渲染图或下载模型。");
      setLoading(false);
      return;
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setClearColor(0x111923, 0);
    container.appendChild(renderer.domElement);
    const scene = new THREE.Scene(),
      camera = new THREE.PerspectiveCamera(40, 1, 0.0001, 100000);
    camera.up.set(0, 0, 1);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    scene.add(new THREE.HemisphereLight(0xe8f5ff, 0x70818d, 3));
    const light = new THREE.DirectionalLight(0xffffff, 4);
    light.position.set(3, -4, 7);
    scene.add(light);
    const fill = new THREE.DirectionalLight(0x82bbf6, 2);
    fill.position.set(-4, 3, 2);
    scene.add(fill);
    const axis = new THREE.AxesHelper(1);
    scene.add(axis);
    let object,
      cancelled = false,
      frame;
    const reset = () => {
      if (!object) return;
      const bounds = new THREE.Box3().setFromObject(object),
        center = bounds.getCenter(new THREE.Vector3());
      const size = Math.max(
        bounds.getSize(new THREE.Vector3()).length(),
        0.001,
      );
      controls.target.copy(center);
      camera.position
        .copy(center)
        .add(new THREE.Vector3(size, -size, size * 0.65));
      camera.near = size / 10000;
      camera.far = size * 1000;
      camera.updateProjectionMatrix();
      axis.position.copy(center);
      axis.scale.setScalar(size * 0.3);
      controls.update();
    };
    const ready = (value) => {
      if (cancelled) {
        value.traverse?.(dispose);
        return;
      }
      object = value;
      scene.add(object);
      reset();
      setLoading(false);
    };
    const failure = () => {
      if (!cancelled) {
        setLoading(false);
        setError("模型加载失败；可下载原始产物或查看对应渲染。");
      }
    };
    const load = (entry) => new Promise((resolve, reject) => {
      const extension = (entry.name ?? name ?? "").split(".").at(-1).toLowerCase();
      if (entry.type === "gaussian_scene") return reject(Error("请在场景页查看真实总览"));
      const finish = (value) => {
        value.userData.part = entry.part;
        value.position.fromArray(entry.offset ?? [0,0,0]);
        value.traverse(item => { if(item.isMesh) {
          item.userData.part = entry.part;
          for(const material of [].concat(item.material)) {
            if(entry.color) material.color.setRGB(...entry.color.slice(0,3));
            material.userData.baseOpacity = material.opacity;
          }
        }});
        resolve(value);
      };
      if(extension === "glb") new GLTFLoader().load(entry.url,r=>finish(r.scene),undefined,reject);
      else if(extension === "obj") new OBJLoader().load(entry.url,finish,undefined,reject);
      else {
        const Loader = extension === "stl" ? STLLoader : PLYLoader;
        new Loader().load(entry.url, geometry => {
          if(entry.type === "point_cloud") {
            finish(new THREE.Points(geometry,new THREE.PointsMaterial({color:0xb5c8ce,size:.005,vertexColors:geometry.hasAttribute("color")})));
          } else {
            geometry.computeVertexNormals();
            finish(new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color:0xb5c8ce,roughness:.5,metalness:.15,side:THREE.DoubleSide})));
          }
        },undefined,reject);
      }
    });
    const entries = objects?.length ? objects : [{url,name,type}];
    const group = new THREE.Group();
    Promise.allSettled(entries.map(load)).then(results => {
      const loaded = results.filter(r => r.status === "fulfilled").map(r=>r.value);
      if(cancelled){loaded.forEach(o=>o.traverse(dispose));return;}
      loaded.forEach(o=>group.add(o));
      if(loaded.length) ready(group); else failure();
      if(loaded.length !== entries.length) setError("部分模型未能加载，可查看渲染或原始产物。");
    });
    const resize = new ResizeObserver(() => {
      const { width, height } = container.getBoundingClientRect();
      renderer.setSize(width, height);
      camera.aspect = width / Math.max(height, 1);
      camera.updateProjectionMatrix();
    });
    resize.observe(container);
    function draw() {
      frame = requestAnimationFrame(draw);
      controls.update();
      renderer.render(scene, camera);
    }
    draw();
    state.current = { reset, scene, axis, renderer, camera, controls };
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      resize.disconnect();
      controls.dispose();
      scene.traverse(dispose);
      renderer.dispose();
      renderer.domElement.remove();
      state.current = null;
    };
  }, [identity]);
  useEffect(() => {
    state.current?.scene.traverse((item) => {
      if (item.isMesh)
        for (const material of [].concat(item.material)) {
          if ("wireframe" in material) material.wireframe = wire;
        }
    });
  }, [wire]);
  useEffect(() => {
    state.current?.scene.traverse(item => {
      if(!item.isMesh) return;
      const match = !selectedPart || item.userData.part === selectedPart;
      for(const material of [].concat(item.material)) {
        material.transparent = !match;
        material.opacity = match ? (material.userData.baseOpacity ?? 1) : .16;
        material.depthWrite = match;
      }
    });
  }, [selectedPart, loading]);
  useEffect(() => {
    if (state.current) state.current.axis.visible = axes;
  }, [axes, loading]);
  return (
    <div className="ec-model">
      <div ref={host} className="ec-model-canvas" />
      {loading && (
        <div className="ec-view-message">
          <Box size={28} />
          <span>正在加载真实模型…</span>
        </div>
      )}
      {error && <div className="ec-view-message">{error}</div>}
      <div className="ec-view-tools">
        <button onClick={() => state.current?.reset()} title="适应视图">
          <Scan size={16} />
        </button>
        <button onClick={() => state.current?.reset()} title="重置视角">
          <RotateCcw size={16} />
        </button>
        <button
          className={wire ? "selected" : ""}
          onClick={() => setWire(!wire)}
          title="线框"
        >
          <Grid2X2 size={16} />
        </button>
        <button
          className={axes ? "selected" : ""}
          onClick={() => setAxes(!axes)}
          title="坐标轴"
        >
          <Axis3D size={16} />
        </button>
      </div>
      <span className="ec-view-caption">
        拖动旋转 · 滚轮缩放 · 资产原始坐标
      </span>
    </div>
  );
}
function dispose(item) {
  item.geometry?.dispose();
  for (const material of [].concat(item.material ?? [])) {
    for (const value of Object.values(material))
      if (value?.isTexture) value.dispose();
    material.dispose();
  }
}
