"""WPS 配置、本人授权与云文档 HTTP 边界。"""

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_admin_user, get_db, get_required_user
from yuxi.modules.identity.models import User
from yuxi.modules.identity.services.cloud_drive_service import CloudDriveService, WPSConfigInput, wps_id_segment
from yuxi.modules.identity.services.wps_development_tunnel import wps_development_tunnel

cloud_drive = APIRouter(prefix="/cloud-drive/wps", tags=["cloud-drive"])


class WPSExchangeInput(BaseModel):
    """浏览器回调传入的一次性授权码与状态。"""

    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=2048, repr=False)
    state: str = Field(min_length=20, max_length=128, repr=False)


# 返回当前临时隧道的公开状态；普通登录用户也需要知道应从哪个域名发起授权。
@cloud_drive.get("/development-tunnel")
async def get_wps_development_tunnel(current_user: User = Depends(get_required_user)):
    """读取 WPS 开发联调地址。"""
    return wps_development_tunnel.status()


# 管理员显式启动命名隧道，服务端固定 cloudflared 命令和 Web 目标。
@cloud_drive.post("/development-tunnel")
async def start_wps_development_tunnel(current_user: User = Depends(get_admin_user)):
    """启动 WPS 开发联调。"""
    return await wps_development_tunnel.start()


# 管理员停止临时隧道并撤销独立回调地址。
@cloud_drive.delete("/development-tunnel")
async def stop_wps_development_tunnel(current_user: User = Depends(get_admin_user)):
    """停止 WPS 开发联调。"""
    return await wps_development_tunnel.stop()


# 隧道启动探针只回显随机 nonce，禁止被浏览器用来探测其它 API。
@cloud_drive.get("/development-tunnel/probe")
async def probe_wps_development_tunnel(nonce: str = Query(..., min_length=20, max_length=128)):
    """验证公网入口是否到达当前 API 进程。"""
    return wps_development_tunnel.probe(nonce)


# 用管理员身份读取共享配置，返回脱敏字段。
@cloud_drive.get("/config")
async def get_wps_config(current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    """读取 WPS 应用配置。"""
    return await CloudDriveService(db).get_config()


# 校验管理员与配置模型后保存，返回数据库最终公开值。
@cloud_drive.put("/config")
async def put_wps_config(payload: WPSConfigInput, current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    """保存 WPS 应用配置。"""
    return await CloudDriveService(db).save_config(payload)


# 当前登录身份拥有连接可见性，普通用户只能查看本人。
@cloud_drive.get("/connection")
async def get_wps_connection(current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """读取本人 WPS 连接。"""
    return await CloudDriveService(db).status(current_user.uid)


# 对本人连接调用 WPS 身份接口重新检查，返回可观察状态。
@cloud_drive.post("/connection/check")
async def check_wps_connection(current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """重新检查本人 WPS 连接。"""
    return await CloudDriveService(db).status(current_user.uid, recheck=True)


# 为当前用户持久化授权状态后装配跳转 URL。
@cloud_drive.post("/authorize")
async def authorize_wps(
    origin: str | None = Header(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """开始本人 WPS 授权。"""
    return await CloudDriveService(db).authorize(current_user.uid, origin)


# 将回调码交给业务服务，只使用当前认证用户的 UID。
@cloud_drive.post("/exchange")
async def exchange_wps(payload: WPSExchangeInput, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """完成本人 WPS 授权。"""
    return await CloudDriveService(db).exchange(current_user.uid, payload.code, payload.state)


# 清除本人凭据，不允许请求体选择其他用户。
@cloud_drive.delete("/connection")
async def disconnect_wps(current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """断开本人 WPS 连接。"""
    return await CloudDriveService(db).disconnect(current_user.uid)


# 将分页参数映射到 WPS 文档库协议。
@cloud_drive.get("/drives")
async def list_wps_drives(
    page_token: str | None = Query(None, max_length=2048),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """读取本人可访问的 WPS 文档库。"""
    params = {"allotee_type": "user", "page_size": 100}
    if page_token:
        params["page_token"] = page_token
    return await CloudDriveService(db).files_request(current_user.uid, "/v7/drives", params)


# 在校验后的盘 ID 与目录 ID 下分页浏览子文件。
@cloud_drive.get("/files")
async def list_wps_files(
    drive_id: str = Query(..., min_length=1, max_length=255),
    parent_id: str = Query("0", min_length=1, max_length=255),
    page_token: str | None = Query(None, max_length=2048),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """浏览 WPS 文件夹。"""
    path = f"/v7/drives/{wps_id_segment(drive_id)}/files/{wps_id_segment(parent_id)}/children"
    params = {"page_size": 100, "with_link": "true"}
    if page_token:
        params["page_token"] = page_token
    return await CloudDriveService(db).files_request(current_user.uid, path, params)


# 按官方文件名搜索协议返回当前用户可访问的结果。
@cloud_drive.get("/search")
async def search_wps_files(
    keyword: str = Query(..., min_length=1, max_length=255),
    page_token: str | None = Query(None, max_length=2048),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """搜索本人 WPS 云文档。"""
    params = {"keyword": keyword, "type": "file_name", "page_size": 100, "with_link": "true"}
    if page_token:
        params["page_token"] = page_token
    return await CloudDriveService(db).files_request(current_user.uid, "/v7/files/search", params)


# 向 WPS 获取文件下载信息，浏览器仅使用返回的签名下载 URL。
@cloud_drive.get("/download")
async def get_wps_download(
    drive_id: str = Query(..., min_length=1, max_length=255),
    file_id: str = Query(..., min_length=1, max_length=255),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户有权下载的 WPS 文件地址。"""
    path = f"/v7/drives/{wps_id_segment(drive_id)}/files/{wps_id_segment(file_id)}/download"
    return await CloudDriveService(db).files_request(current_user.uid, path)
