// Jokate revision control plugin for Unreal Engine.

#include "JokateSourceControlState.h"

#if SOURCE_CONTROL_WITH_SLATE
#include "RevisionControlStyle/RevisionControlStyle.h"
#include "Textures/SlateIcon.h"
#endif

#define LOCTEXT_NAMESPACE "JokateSourceControl.State"

int32 FJokateSourceControlState::GetHistorySize() const
{
	return History.Num();
}

TSharedPtr<class ISourceControlRevision, ESPMode::ThreadSafe> FJokateSourceControlState::GetHistoryItem(int32 HistoryIndex) const
{
	if (History.IsValidIndex(HistoryIndex))
	{
		return History[HistoryIndex];
	}
	return nullptr;
}

TSharedPtr<class ISourceControlRevision, ESPMode::ThreadSafe> FJokateSourceControlState::FindHistoryRevision(int32 RevisionNumber) const
{
	for (const auto& Revision : History)
	{
		if (Revision->GetRevisionNumber() == RevisionNumber)
		{
			return Revision;
		}
	}
	return nullptr;
}

TSharedPtr<class ISourceControlRevision, ESPMode::ThreadSafe> FJokateSourceControlState::FindHistoryRevision(const FString& InRevision) const
{
	for (const auto& Revision : History)
	{
		if (Revision->GetRevision() == InRevision)
		{
			return Revision;
		}
	}
	return nullptr;
}

TSharedPtr<class ISourceControlRevision, ESPMode::ThreadSafe> FJokateSourceControlState::GetCurrentRevision() const
{
	// 이력은 최신순이므로 첫 항목이 마지막 확정 버전이다.
	if (History.Num() > 0)
	{
		return History[0];
	}
	return nullptr;
}

#if SOURCE_CONTROL_WITH_SLATE
FSlateIcon FJokateSourceControlState::GetIcon() const
{
	switch (State)
	{
	case EJokateFileState::Modified:
		return FSlateIcon(FRevisionControlStyleManager::GetStyleSetName(), "RevisionControl.CheckedOut");
	case EJokateFileState::Added:
		return FSlateIcon(FRevisionControlStyleManager::GetStyleSetName(), "RevisionControl.OpenForAdd");
	case EJokateFileState::Deleted:
	case EJokateFileState::Missing:
		return FSlateIcon(FRevisionControlStyleManager::GetStyleSetName(), "RevisionControl.MarkedForDelete");
	case EJokateFileState::Untracked:
		return FSlateIcon(FRevisionControlStyleManager::GetStyleSetName(), "RevisionControl.NotInDepot");
	default:
		return FSlateIcon();
	}
}
#endif

FText FJokateSourceControlState::GetDisplayName() const
{
	switch (State)
	{
	case EJokateFileState::Modified:
		return LOCTEXT("Modified", "확정 안 된 변경");
	case EJokateFileState::Added:
		return LOCTEXT("Added", "새 애셋(확정 전)");
	case EJokateFileState::Deleted:
		return LOCTEXT("Deleted", "삭제됨(확정 전)");
	case EJokateFileState::Missing:
		return LOCTEXT("Missing", "파일 없음");
	case EJokateFileState::Clean:
		return LOCTEXT("Clean", "확정됨");
	case EJokateFileState::Untracked:
		return LOCTEXT("Untracked", "추적 안 함");
	default:
		return LOCTEXT("Unknown", "알 수 없음");
	}
}

FText FJokateSourceControlState::GetDisplayTooltip() const
{
	switch (State)
	{
	case EJokateFileState::Modified:
		return LOCTEXT("Modified_Tooltip", "마지막 확정 이후 바뀐 애셋입니다.");
	case EJokateFileState::Added:
		return LOCTEXT("Added_Tooltip", "아직 한 번도 확정하지 않은 새 애셋입니다. 변경 버리기(Revert)를 하면 파일이 지워집니다.");
	case EJokateFileState::Deleted:
		return LOCTEXT("Deleted_Tooltip", "확정된 뒤 지워진 애셋입니다.");
	case EJokateFileState::Missing:
		return LOCTEXT("Missing_Tooltip", "디스크에서 파일을 찾을 수 없습니다.");
	case EJokateFileState::Clean:
		return LOCTEXT("Clean_Tooltip", "마지막 확정 상태와 같습니다.");
	case EJokateFileState::Untracked:
		return LOCTEXT("Untracked_Tooltip", "Jokate 가 추적하지 않는 파일입니다.");
	default:
		return LOCTEXT("Unknown_Tooltip", "Jokate 데몬에서 상태를 받지 못했습니다.");
	}
}

const FString& FJokateSourceControlState::GetFilename() const
{
	return LocalFilename;
}

const FDateTime& FJokateSourceControlState::GetTimeStamp() const
{
	return TimeStamp;
}

bool FJokateSourceControlState::CanCheckIn() const
{
	return State == EJokateFileState::Modified
		|| State == EJokateFileState::Added
		|| State == EJokateFileState::Deleted;
}

bool FJokateSourceControlState::CanCheckout() const
{
	return false;
}

bool FJokateSourceControlState::IsCheckedOut() const
{
	return false;
}

bool FJokateSourceControlState::IsCheckedOutOther(FString* Who) const
{
	if (Who != nullptr)
	{
		Who->Empty();
	}
	return false;
}

bool FJokateSourceControlState::IsCurrent() const
{
	return true;
}

bool FJokateSourceControlState::IsSourceControlled() const
{
	return State != EJokateFileState::Untracked
		&& State != EJokateFileState::Missing
		&& State != EJokateFileState::Unknown;
}

bool FJokateSourceControlState::IsAdded() const
{
	return State == EJokateFileState::Added;
}

bool FJokateSourceControlState::IsDeleted() const
{
	return State == EJokateFileState::Deleted;
}

bool FJokateSourceControlState::IsIgnored() const
{
	return false;
}

bool FJokateSourceControlState::CanEdit() const
{
	// 체크아웃 개념이 없으므로 언제나 편집 가능하다.
	return true;
}

bool FJokateSourceControlState::CanDelete() const
{
	return IsSourceControlled();
}

bool FJokateSourceControlState::IsUnknown() const
{
	return State == EJokateFileState::Unknown;
}

bool FJokateSourceControlState::IsModified() const
{
	return State == EJokateFileState::Modified
		|| State == EJokateFileState::Added
		|| State == EJokateFileState::Deleted;
}

bool FJokateSourceControlState::CanAdd() const
{
	return false;
}

bool FJokateSourceControlState::CanRevert() const
{
	return CanCheckIn();
}

bool FJokateSourceControlState::IsConflicted() const
{
	return false;
}

#undef LOCTEXT_NAMESPACE
