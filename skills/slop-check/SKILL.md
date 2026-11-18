---
name: slop-check
description: Detect AI-generated design clichés in web UI code. Use when a landing page or component looks "AI-generated", when reviewing frontend visual quality, or before shipping UI and you need concrete color, layout, copy, and CSS fixes.
---

# slop-check

用规则扫描前端代码里的「AI 味」套路，给出可执行的替换建议，而不是一句「设计感不够」。

## 何时使用

- 页面生成完，想看它是否落进了模板化审美
- 评审落地页、组件库、营销页的视觉质量
- 面试作品、外包交付前的自检
- CI 里给前端加一道「审美门禁」

## 运行

```bash
python scripts/slop_check.py src/
python scripts/slop_check.py dist/index.html --json
python scripts/slop_check.py src/ --threshold 70   # 低于 70 分返回非零
python scripts/slop_check.py src/ --include-js     # 连 .js/.ts 一起扫
```

支持 `.html` `.css` `.scss` `.less` `.jsx` `.tsx` `.vue` `.svelte` `.astro`；
默认跳过 `node_modules`、`dist`、`build`、`.next`。

## 评分口径

- 满分 100，每条规则按权重扣分，同一规则只扣一次
- 等级：A ≥ 85，B ≥ 70，C ≥ 55，D ≥ 40，F < 40
- 每条结论都带 `文件:行号`、命中原文和修复建议
- `--threshold N`：分数低于 N 时退出码为 1，可直接做 CI 门禁

## 检测维度

| 维度 | 典型表现 |
| --- | --- |
| 配色 | 紫→粉渐变、渐变文字、无来由的光斑 |
| 版式 | 居中大标题英雄区、三卡片网格、胶囊渐变按钮 |
| 装饰 | 玻璃拟态、全大写细字距小标签、大圆角长阴影 |
| 文案 | 「赋能」「一站式」「无缝」「重新定义」等空话，lorem ipsum |
| 数据 | 99.9%、10x、100+ 这类没有出处的漂亮数字 |

## 输出约定

- 默认文本输出：总分 → 命中规则（含证据与修复）→ 最优先修复的三条
- `--json`：`score`、`grade`、`findings[]`、`by_rule{}` 结构化输出
- 退出码：0 正常；1 低于阈值；2 路径或参数错误

## 坑点

- 这是启发式检测，不是设计评审：品牌色本身就是紫色时，AI-001 会误报，需人工确认
- 单行正则匹配，跨行书写的渐变可能漏检
- 分数高低不代表设计好坏，只代表「套路密度」高低
