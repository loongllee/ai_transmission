"""多级组织管理：学院 / 专业 / 课题组（方案第四阶段）。

OrgUnit 通过 parent_id 形成树；用户经 OrgMembership 绑定到一个叶子单元，
祖先单元通过 parent_id 推导。提供建树、成员绑定与按单元（含子树）的用量汇总。
"""
from typing import List, Optional, Set

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import OrgMembership, OrgUnit, UsageLog, User

VALID_TYPES = ("college", "major", "group")


def create_unit(
    db: Session,
    name: str,
    unit_type: str,
    parent_id: Optional[int] = None,
    code: Optional[str] = None,
) -> OrgUnit:
    if unit_type not in VALID_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unit_type 须为 {VALID_TYPES}")
    if parent_id and not db.get(OrgUnit, parent_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "父级组织不存在")
    unit = OrgUnit(name=name, unit_type=unit_type, parent_id=parent_id, code=code, status="active")
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return unit


def list_units(db: Session) -> List[OrgUnit]:
    return db.query(OrgUnit).order_by(OrgUnit.id.asc()).all()


def _children_map(db: Session):
    children = {}
    for u in db.query(OrgUnit).all():
        children.setdefault(u.parent_id, []).append(u.id)
    return children


def descendant_ids(db: Session, unit_id: int) -> Set[int]:
    """返回包含自身在内的子树单元 id 集合。"""
    children = _children_map(db)
    out: Set[int] = set()
    stack = [unit_id]
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack.extend(children.get(cur, []))
    return out


def set_membership(db: Session, user_id: int, org_unit_id: int) -> OrgMembership:
    if not db.get(OrgUnit, org_unit_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "组织单元不存在")
    m = db.query(OrgMembership).filter(OrgMembership.user_id == user_id).first()
    if m:
        m.org_unit_id = org_unit_id
    else:
        m = OrgMembership(user_id=user_id, org_unit_id=org_unit_id)
        db.add(m)
    db.commit()
    db.refresh(m)
    return m


def find_or_create(db: Session, name: str, unit_type: str, parent_id: Optional[int]) -> OrgUnit:
    q = db.query(OrgUnit).filter(OrgUnit.name == name, OrgUnit.unit_type == unit_type)
    q = q.filter(OrgUnit.parent_id == parent_id) if parent_id else q.filter(OrgUnit.parent_id.is_(None))
    found = q.first()
    if found:
        return found
    unit = OrgUnit(name=name, unit_type=unit_type, parent_id=parent_id, status="active")
    db.add(unit)
    db.flush()
    return unit


def ensure_path(db: Session, college: str, major: str = None, group: str = None) -> Optional[int]:
    """确保 学院→专业→课题组 路径存在，返回最深一层（叶子）的 id。"""
    if not college:
        return None
    c = find_or_create(db, college, "college", None)
    leaf = c
    if major:
        m = find_or_create(db, major, "major", c.id)
        leaf = m
        if group:
            g = find_or_create(db, group, "group", m.id)
            leaf = g
    db.commit()
    return leaf.id


def rollup_stats(db: Session) -> List[dict]:
    """每个组织单元（含子树）的成员数、调用数、token 数汇总（方案学校级报表）。"""
    units = list_units(db)
    # 预取成员归属
    memberships = db.query(OrgMembership).all()
    members_by_unit = {}
    for m in memberships:
        members_by_unit.setdefault(m.org_unit_id, []).append(m.user_id)
    out = []
    for u in units:
        sub = descendant_ids(db, u.id)
        user_ids = [uid for unit in sub for uid in members_by_unit.get(unit, [])]
        calls = 0
        tokens = 0
        if user_ids:
            calls = db.query(UsageLog).filter(UsageLog.user_id.in_(user_ids)).count()
            tokens = int(
                db.query(func.coalesce(func.sum(UsageLog.input_tokens + UsageLog.output_tokens), 0))
                .filter(UsageLog.user_id.in_(user_ids))
                .scalar()
                or 0
            )
        out.append(
            {
                "id": u.id,
                "name": u.name,
                "unit_type": u.unit_type,
                "parent_id": u.parent_id,
                "members": len(set(user_ids)),
                "calls": calls,
                "tokens": tokens,
            }
        )
    return out
