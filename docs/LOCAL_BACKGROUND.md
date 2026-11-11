# 本地背景扩图

本模块补齐操作区域之外的视觉背景，复用既有图片和背景 mask。任务物体仍由原有 Blender／Artisan 资产表示；不生成房间碰撞、关节、物理参数或新的 3DGS。

## 入口和调用关系

```text
envcreator background
  ├─ construction/background_cli.py：已有素材的独立入口
  ├─ background_generation.py：参考图、背景 mask、画布和来源记录
  ├─ background_generation_worker.py：本地 SD1.5 Inpainting，单独进程退出释放显存
  └─ 可选 background_scene.py → background_scene_worker.py
      └─ 在原 USD 上叠加固定相机背景板，不修改原资产与物理属性

envcreator generate --background-generation-config <toml>
  └─ harness/run.py:_background → 同一生成实现
      └─ 背景图单独交付，不自动选择相机或替换物理场景
```

生成使用 Diffusers 的 `StableDiffusionInpaintPipeline`。VLM 不参与扩散推理，也不需要 VLM／DSH 凭据。模型已存在本地时，生成阶段使用 `local_files_only=True`，不会临时联网下载权重。

## 安装

先按本仓库 CUDA 环境说明准备 PyTorch，然后安装可选依赖：

```bash
uv pip install --python .venv/bin/python -e '.[background-generation]'
.venv/bin/python scripts/download_background_model.py \
  --output /absolute/model-storage/sd15-inpainting
```

下载入口固定公开模型 `stable-diffusion-v1-5/stable-diffusion-inpainting` 的 revision `8a4288a76071f7280aedbdb3253bdb9e9d5d84bb`，只获取半精度 safetensors、配置、tokenizer 和模型卡。匿名下载，不读取 Hugging Face、EnvCreator 或 DSH 密钥；写出文件哈希，LFS 文件还与上游声明的 SHA-256 比对。模型权重不放入 Git。

复制 `configs/background-generation.example.toml` 为本地配置，填写 `model_path`。默认 768×512、单张、30 步、半精度；可打开 `cpu_offload` 降低显存占用，代价是速度。真实峰值和质量以逐次记录为准。

## 使用已有背景包

```bash
.venv/bin/envcreator background \
  --config configs/local-background-generation.toml \
  --prepared-background /absolute/path/to/prepared-background \
  --frame-id frame-000000 \
  --output outputs/background-NEW
```

输入目录含 `background.json` 及其图像、observed mask。没有指定帧时选已有记录中 `observed_pixels` 最多的一帧。默认生成流程不加载扩图模型；只有指定独立命令或 Harness 的可选配置时才执行。

也可提供单张真实图片和经过复核的背景 mask：

```bash
.venv/bin/envcreator background \
  --config configs/local-background-generation.toml \
  --image /absolute/path/to/frame.png \
  --observed-mask /absolute/path/to/observed.png \
  --output outputs/background-NEW
```

mask 必须为原图大小的单通道二值图：255 表示可保留的真实静态背景，0 表示待补全。背景并非前景的自动补集；未知区域仍需正确处理。排除将由独立资产渲染的笔记本、瓶子、鼠标、机器人、手和工具，避免背景残留副本。原背景包只认可桌面时，不会自动把其他未知像素升级成观测；人工／Agent 复核过的覆盖通过单独 mask 输入，并保留其来源。

## 固定相机加载

在以上命令追加：

```bash
  --scene /absolute/path/to/scene.usda \
  --camera /World/Overview \
  --usd-python /absolute/path/to/python-with-usd-core \
  --display-distance 4
```

入口将背景作为没有碰撞的发光显示板放在指定透视相机后景，中心裁剪以匹配相机比例，并引用原 USD。它只有四个显示顶点，不是重建的房间 mesh；原场景不被覆盖。工作进程重新打开结果，并逐项比较原场景属性、关节、关系及连接，验证没有加入物理组件或修改原图层。

这是一种固定视角显示方式。普通扩图不是无缝全景，也没有近景视差。移动／转动相机离开适用范围后背景可能穿帮；图片来源相机与模拟器相机未标定时，布局也只是外观候选。背景板自身会出现在渲染深度中，该深度是显示距离，不能作为房间测量或导航障碍；用于深度任务时须排除显示层。发光材质可能影响间接照明，实际效果需检查。

若已有仿真地面挡住背景图片，可显式追加 `--hide-floor-visual /World/Floor`，只在新图层将指定地面的 `visibility` 设为 `invisible`，保留碰撞。结构比较会单独排除这一个明确要求的显示属性，其他原有属性仍须一致。该方式也会失去原地面的可见阴影；不适合当成经过几何标定的真实地面。

## 产物和复核

每次使用新的输出目录。`generation/` 保存：输入画布、生成 mask、原始生成图、合成背景、来源图、模型文件哈希、配置及运行日志。真实保留区按缩放后的输入逐像素合回；边界过渡单列。`provenance.png` 中 0=生成、1=缩放后的观测保留区、2=混合边界。种子、模型和库版本绑定每次结果，不承诺跨硬件完全逐位一致。

`display/` 保存场景和背景图片及加载前的结构检查。`run.json`／`artifacts.json` 记录每个阶段及哈希；失败不标成成功，生成图的 `visual_review` 默认仍为 `pending`。

可使用 `construction/background_render_worker.py` 在现有笔记本场景中执行真实 Isaac 相机重载：同一状态分别关闭／开启背景，保存 RGB；随后运行原有物体的短程稳定性检查。它是笔记本实例的验证入口，不是任意场景都适用的通用机器人实验。展示图、结构检查、Isaac 物理检查分别记录，不把扩图成功当作机器人任务完成。
