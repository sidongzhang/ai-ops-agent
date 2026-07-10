"""注册 / 登录。首个用户注册时创建其 Org（owner）。"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select

from ..core.database import get_session
from ..core.deps import get_current_user
from ..models.auth import Org, User
from ..schemas import RegisterRequest, TokenResponse, UserOut
from ..core.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest, session: Session = Depends(get_session)):
    exists = session.exec(select(User).where(User.email == body.email)).first()
    if exists:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该邮箱已注册")

    org = Org(name=body.org_name)
    session.add(org)
    session.commit()
    session.refresh(org)

    user = User(email=body.email, hashed_password=hash_password(body.password),
                org_id=org.id, role="owner")
    session.add(user)
    session.commit()
    session.refresh(user)

    return TokenResponse(access_token=create_access_token(user.id, user.org_id))


@router.post("/login", response_model=TokenResponse)
def login(form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.email == form.username)).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    return TokenResponse(access_token=create_access_token(user.id, user.org_id))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(id=user.id, email=user.email, org_id=user.org_id, role=user.role)


@router.get("/users", response_model=list[UserOut])
def list_org_users(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    users = session.exec(select(User).where(User.org_id == user.org_id).order_by(User.id.asc())).all()
    return [UserOut(id=item.id, email=item.email, org_id=item.org_id, role=item.role) for item in users]
