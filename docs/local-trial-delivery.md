# AI-TAgent 本地基础版候选试用包

## 当前结论

这是独立、失败关闭的**离线试用候选配置**，不是已部署产品或生产发布。
本次环境没有 Docker，未启动容器，未证明浏览器、重启持久化或真实模型可用。
不得将静态测试、MockTransport 或替身模型报告为真实企业验收通过。

只针对单实例、SQLite、本地账号、人工审批、报告验收的基础版本。
参考 `fresh-install-inspection-acceptance.md` 的已自动化验证链路。
AKB 检索保持关闭；OIDC、真实网关、真实采集、内容审计与企业高可用不在本包验收范围。

## 与现有部署完全分开

使用 `docker/local-trial/compose.yml`，**不要**与默认 Compose 合并。
使用专用新卷 `ai-tagent-local-trial_trial-data`，不挂载现有 ~/.octop、旧数据库、
Docker socket、宿主目录或凭据，不连接现有企业环境。
仅宿主 `127.0.0.1:8088` 可访问；容器内部监听 0.0.0.0 是端口映射所需，
并非宿主对外开放。云环境须使用经过认证的开发空间端口转发，不直接公开端口。

内部 Docker 网络禁止外网路由；不启用额外网络或 host 网络。它不是恶意代码的完整安全边界，
Docker 宿主仍需可靠隔离。沿用现有镜像的容器 root 身份；同一进程和卷内的多用户/Agent
共享执行环境，并非租户级沙箱。不要存放企业秘密。根文件系统只读、移除 capabilities，
无 privileged 模式；数据仅写独立卷，临时文件仅写 tmpfs。

没有默认管理员密码、模型密钥或第三方令牌，不继承宿主 .env。
首次管理员走 Web 向导；向导口令由原入口随机生成到卷内，不应提交、截图或粘贴到报告。
遥测明确关闭。`OCTOP_LOCAL_TRIAL=1` 抑制主动关怀调度和直接执行，并禁止自动安装 bubblewrap。
不要在环境变量管理中修改上述开关，也不要导入卷内 `env` 文件：应用会将其覆盖到进程环境。
此配置不对管理员主动修改策略提供防篡改保证。插件加载器也可能尝试安装依赖，
内部网络和只读根文件系统可能使其失败；不得将本包描述成“没有任何后台安装尝试”。
它**不是**全面暂停后台 worker 的开关：cron、索引、桥接等机制依然存在，因此必须使用新空卷，
不要导入旧任务、绑定 IM、桥接或主动连接器。网络层阻止对外请求，不保证所有尝试都不发生。

## 启动前门槛（由开发人员在允许 Docker 的环境操作）

1. 确认 Docker / Compose 可用。不可用则停止，不自动安装，不切换到裸机 `octop run` 绕过。
2. 先阅读现有 `docker/Dockerfile`。它会在**构建时**联网下载依赖，且目前不自带 bubblewrap。
   本包不自动构建、不拉取镜像、不安装软件。开发人员需在批准的构建环境准备包含本分支代码、
   已构建前端和受审核 bubblewrap 的镜像，再将唯一镜像标签传入。旧镜像不含试用策略，不得使用。
3. 运行预检配置命令，然后人工确认卷是全新的、无敏感环境变量：

   ```sh
   export AI_TAGENT_TRIAL_IMAGE=your-reviewed-local-image:unique-build-tag
   docker compose -f docker/local-trial/compose.yml config
   docker compose -f docker/local-trial/compose.yml up -d --no-build --pull never
   docker compose -f docker/local-trial/compose.yml logs --tail 50
   ```

4. 入口首先实际运行 `bwrap --unshare-all` 探针。不存在或内核禁止则退出；
   **不要**添加 privileged、SYS_ADMIN、放开 seccomp、禁用沙箱或自动 apt 安装来绕过。
   默认镜像会因缺 bubblewrap 而停止，这是预期保护，不是成功部署。
   探针成功仅证明一次 namespace 操作可用，**不保证每个 Agent 都在 bubblewrap 中执行**：
   原有 host-root / virtual_mode=false 等路径仍可能使用 local_shell。容器边界才是本包主要隔离；
   禁止在此试用中运行不可信代码、导入第三方技能或启用自主工具执行。
5. 仅探针及启动成功后访问 `http://127.0.0.1:8088`。在本机私密终端读取
   卷内 `.octop/octop-login.txt` 完成向导，自己设置强密码；不要使用演示账号/固定密码。
   外部模型、HTTP 数据源本来就无法在此离线网络调用。向导中若需模型验证而无法继续，
   停止并记录阻断，不使用伪造成功响应或虚构 API Key。

## 验收与下一阶段

实际执行后记录镜像标签、分支 SHA、环境、启动日志（脱敏）、登录/退出、独立审批、
重启前后数据一致性、备份恢复结果；失败项目标为阻断，不能用静态测试代替。
需要真实模型/数据源时，应在独立集成环境人工配置许可目标、密钥、预算和出站白名单，
逐项批准连接及数据共享，再跑同一业务链路。不要直接把此内部网络改成全开放。

停止而保留数据：

```sh
docker compose -f docker/local-trial/compose.yml stop
```

不得默认运行 `down -v`，这会删除试用数据。备份须在服务停止后按现有备份操作规程执行，
恢复到另一个新卷并验证。该包不承诺云环境长期保留，远端代码和可复现配置不等于运行环境已持久化。
