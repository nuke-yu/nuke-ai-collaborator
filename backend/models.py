from pydantic import BaseModel
from typing import Optional

class SendMessageRequest(BaseModel):
    member_id: int
    content: str

class AddMemberRequest(BaseModel):
    name: str
    type: str
    role: Optional[str] = None
    system_prompt: Optional[str] = None
    avatar_color: Optional[str] = "#6366f1"
    model_provider: Optional[str] = "deepseek"
    model_name: Optional[str] = "deepseek-chat"

class CreateGroupRequest(BaseModel):
    name: str

class UpdateGroupRequest(BaseModel):
    name: Optional[str] = None
    announcement: Optional[str] = None

class EditMessageRequest(BaseModel):
    content: str
    member_id: int

class ReactionRequest(BaseModel):
    member_id: int
    emoji: str

class RoleTemplateRequest(BaseModel):
    name: str
    role: str
    system_prompt: str
    avatar_color: Optional[str] = "#6366f1"
