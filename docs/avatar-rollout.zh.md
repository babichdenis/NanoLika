# 管理员控制默认 Lika

管理页为 `/app/avatar/`，使用现有 `X-Admin-Token`，密钥仅保存在当前页面内存。上传只添加预览套装，独立确认后才发布。可恢复上一个套装或回到内置形象。此功能不写入账户资料，也不会替换个人形象、表情头像或自有视频。

ZIP 包含带短 label 的 manifest.json、五张 768×768 WebP 图片及四段无声 512×512 H.264 MP4。图片名称为 idle、working、waiting、happy、error；error 无需视频。视频时长为 10 至 15 秒。服务限制压缩及解压大小，要求平铺且完整的文件列表，拒绝重复、缺失及路径穿越，并验证媒体头、轨道数、尺寸、编码和时长。这不能证明艺术一致性、无缝循环或像素质量，必须人工预览。此存储仅保存品牌媒体，不存放客户文档。

AVATAR_RELEASES_DIR 默认为数据库旁的 avatar-releases，必须持久化并备份。套装使用内容派生的不可变 ID 和每个文件的 SHA-256。完整写入后才允许选择，active.json 原子更新，expected_revision 防止同一进程中的冲突。当前文件锁仅支持单个中继发布进程；多个进程需要共享事务锁。保留旧套装以支持不可变地址及回退，清理时必须考虑当前、上一个套装和客户端缓存。

公开只读接口：GET /v1/brand/avatar 返回不缓存的当前清单；GET /v1/brand/avatar/assets/{release}/{filename} 返回白名单媒体。/web/avatar-preview/?release=<id> 只在用户点击播放后播放真实视频，不改变当前套装。error 为静态图片。

管理员接口：GET /v1/admin/avatar-releases，POST /v1/admin/avatar-releases/upload 接收 ZIP，POST /v1/admin/avatar-releases/publish 接收 release 和 expected_revision。release 为 null 时恢复内置形象，过期版本返回 409。公开清单和媒体不包含管理员凭据。

Cloud PWA、运行时 Web 界面及桌面插件源码已加入清单读取。只有默认形象使用发布套装，个人形象及视频优先。可见时约每分钟检查一次，服务器或文件不可用时使用本地内置媒体，并尊重减少动态效果偏好。内部 dragon 标识保持不变。PWA 只缓存代码外壳，不缓存动态清单及品牌媒体，离线仍可显示内置 Lika。

已安装的旧客户端必须正常更新一次以支持此协议，服务器无法为旧客户端自动补上读取逻辑。本次未实现 Android/iOS 读取器，未构建原生安装包。管理员界面明确提示支持条件，发布不会修改账户偏好或分发可执行代码。

测试覆盖上传、非法文件、鉴权、发布/恢复、缓存头及版本冲突。浏览器验证真实视频及不同屏宽，发布按钮通过隔离 API 替身验证，不对真实客户发布。部署使用无网络 canary，并保留支付、退款和机器人网关覆盖层。
