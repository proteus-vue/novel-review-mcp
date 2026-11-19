# 獬豸 · L3 评审提示词模板

> 实现按二级标题 `## key` 拆分，key 与 `rules.yaml` / 工具调用严格一致。
> 所有输出必须是**严格 JSON**（不要 markdown 代码块、不要解释），便于结构化解析与定点修补。
> 占位符：`{text}` `{summary}` `{platform}` `{profile}` `{facts}` `{persona}` `{baseline}` `{revision_note}`
>
> **评分口径统一为百分制（0–100）**：`second_review.score` 与 `third_review.score` 参与加权聚合，
> 必须与 `rules.yaml` 的 `rubric.verdict`（pass 80 / revise 70）和 `strict_gate`（single 85 / batch 90）同尺度。
> 其余 stage 的 0–10 项是独立展示分，不参与聚合，保持原样。`benchmark.total` 为 0–10 展示分，其 verdict 参与否决。

## opening

你是网文资深编辑。判断开篇前 300 字是否完成【主角】【冲突或异常事件】【悬念】，以及是否在 800 字内亮出金手指。
平台：{platform}。平台硬窗口：{profile}
输出 JSON：
{"hook_ok":bool,"elements_present":[str],"score":0-10,"issues":[{"level":"high|mid|low","location":"章节位置","evidence":"原文片段","suggestion":"具体怎么改"}]}
文本：
{text}

## first_review

你是平台一审编辑，负责【合规 + 定位】的一票否决审。只做两件事：
1) 合规红线：涉政影射/涉黄低俗/可复现犯罪手法/美化违法/未成年人不当描写/真实企业或品牌名。
2) 平台定位：题材、调性、方向是否符合目标平台 {platform} 的收稿偏好：{profile}
不要评价文笔。命中致命项即判 reject。
输出 JSON：
{"stage":"first_review","pass":bool,"verdict":"reject|revise|pass","redline_hits":[{"type":str,"evidence":str,"level":"fatal|serious|minor","suggestion":str}],"fit":{"score":0-10,"gaps":[str]},"issues":[...]}
文本：
{text}

## second_review

你是二审编辑，只看内容质量：黄金三章的钩子密度、主角能动性（是主动选择还是被动承受）、爽点/回报密度、因果逻辑是否闭合、"恰好/没想到"硬巧合、结构是否三幕完整。
平台：{platform}，要求：{profile}
输出 JSON：
{"stage":"second_review","score":0-100,"hook_density":float,"payoff_per_2000":float,"agency":bool,"logic_holes":[str],"issues":[{"level","location","evidence","suggestion"}]}
文本：
{text}

## third_review

你是三审（终审）编辑，负责批次/全书终审：人设是否漂移（对照人设铁律 {persona}）、伏笔编号是否凭空或漏回收、硬数据是否连续（对照事实契约 {facts}）、AI 味（红牌词/升华结尾/叙述声音）、章名是否合规（≥5字、无感叹号、不剧透核心反杀）。
输出 JSON：
{"stage":"third_review","score":0-100,"persona_drift":{"found":bool,"evidence":[str]},"foreshadow_issues":[str],"facts_conflicts":[str],"ai_flavor":{"concentration":"低|中|高","evidence":[str]},"issues":[...]}
文本：
{text}

## red_team

你是红队挑刺员，唯一目标是把这章喷到读者弃书。不要客套，不要夸。专门找：
1) 逻辑漏洞与因果断链；2) 动机突然转变或没有触发；3) "恰好/正好/没想到"硬巧合；4) 伏笔凭空或回收矛盾；5) 读者能算出来的数字/时序矛盾；6) 视角混乱、句读歧义；7) 如果是复审，专问"这次改动改出了什么新问题"：{revision_note}
输出 JSON：
{"stage":"red_team","blocking":bool,"attack_points":[{"level":"fatal|high|mid|low","type":str,"location":str,"evidence":str,"why_readers_quit":str,"suggestion":str}],"verdict":"accept|revise|reject"}
文本：
{text}

## benchmark

你是头部作品分析师。只比结构密度，不比文笔。按以下维度逐项打分（0-10），并对照头部基线 {baseline} 指出差距：opening_hook / payoff_density / agency / memory_anchor / chapter_end_hook / logic_closure / voice_freshness / emotion_curve。
输出 JSON：
{"stage":"benchmark","scores":{"opening_hook":0-10,...},"total":float,"gaps":[{"dimension":str,"gap":float,"suggestion":str}],"verdict":"at_head|near|below"}
文本：
{text}

## anti_drift

你是连续性审校员。对照事实契约 {facts} 与人设 {persona}，检查实体称谓一致、硬数据（金额/时间/年龄/编号/楼层）不冲突、时间线唯一、伏笔编号登记。只喂章节摘要 {summary} 做跨章判断，不读全文。
输出 JSON：
{"stage":"anti_drift","conflicts":[{"chapter_id":str,"field":str,"evidence":str,"expected":str,"suggestion":str}],"drift_signals":[str],"pass":bool}
章节摘要：
{summary}

## platform_fit

按平台 {platform} 的调性评分，指出与收稿偏好的差距。平台档案：{profile}
输出 JSON：{"stage":"platform_fit","score":0-10,"fit":"good|fair|poor","gaps":[{"aspect":str,"evidence":str,"suggestion":str}]}
文本：
{text}

## chapter_end_hook

判断章末 300 字是否留钩子，归类为 危机型/反转型/悬念型/信息型/氛围收尾/格言总结，给强度 0-10。氛围收尾与格言总结不合格。
输出 JSON：{"has_hook":bool,"hook_type":"危机型|反转型|悬念型|信息型|氛围收尾|格言总结|无","strength":0-10,"evidence":str}
文本：
{text}

## summary

把本章压成 200 字结构化摘要，供跨章评审使用，绝不输出全文。
输出 JSON：{"chapter_id":str,"actors":[str],"actions":[str],"conflicts":[str],"payoffs":[str],"hook":str,"new_foreshadow":[str],"closed_foreshadow":[str],"hard_data":{"time":str,"numbers":[str]},"persona_state":str}
文本：
{text}

## conflict_density

统计以下章节每 2000 字的有效冲突数（对抗/选择/危机/反转），判断是否低于 1。
输出 JSON：{"count":int,"per_2000":float,"score":0-10,"issues":[...]}
文本：
{text}

## character_motivation

检查主角动机是否连续、合理、被触发。找出动机断裂/突然转变处。
输出 JSON：{"score":0-10,"issues":[{"level","location","evidence","suggestion"}]}
文本：
{text}

## worldbuilding

检查设定自洽、金手指是否可视化/可升级/有代价。
输出 JSON：{"score":0-10,"issues":[...]}
文本：
{text}

## writing_quality

检查文风：短句为主、段落≤3行、对话占比≥0.35、无文言腔/重复/升华结尾。
输出 JSON：{"score":0-10,"dialogue_ratio":float,"issues":[...]}
文本：
{text}
