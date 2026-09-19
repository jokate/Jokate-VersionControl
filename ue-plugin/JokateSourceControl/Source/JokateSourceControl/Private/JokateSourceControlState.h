// Jokate revision control plugin for Unreal Engine.

#pragma once

#include "CoreMinimal.h"
#include "ISourceControlState.h"
#include "ISourceControlRevision.h"
#include "JokateSourceControlRevision.h"

/** Jokate 데몬이 알려주는 파일 상태. */
enum class EJokateFileState : uint8
{
	Unknown,
	Clean,
	Modified,
	Added,
	Deleted,
	Untracked,
	Missing
};

class FJokateSourceControlState : public ISourceControlState
{
public:
	explicit FJokateSourceControlState(const FString& InLocalFilename)
		: LocalFilename(InLocalFilename)
	{
	}

	// ISourceControlState
	virtual int32 GetHistorySize() const override;
	virtual TSharedPtr<class ISourceControlRevision, ESPMode::ThreadSafe> GetHistoryItem(int32 HistoryIndex) const override;
	virtual TSharedPtr<class ISourceControlRevision, ESPMode::ThreadSafe> FindHistoryRevision(int32 RevisionNumber) const override;
	virtual TSharedPtr<class ISourceControlRevision, ESPMode::ThreadSafe> FindHistoryRevision(const FString& InRevision) const override;
	virtual TSharedPtr<class ISourceControlRevision, ESPMode::ThreadSafe> GetCurrentRevision() const override;
#if SOURCE_CONTROL_WITH_SLATE
	virtual FSlateIcon GetIcon() const override;
#endif
	virtual FText GetDisplayName() const override;
	virtual FText GetDisplayTooltip() const override;
	virtual const FString& GetFilename() const override;
	virtual const FDateTime& GetTimeStamp() const override;
	virtual bool CanCheckIn() const override;
	virtual bool CanCheckout() const override;
	virtual bool IsCheckedOut() const override;
	virtual bool IsCheckedOutOther(FString* Who = nullptr) const override;
	virtual bool IsCheckedOutInOtherBranch(const FString& CurrentBranch = FString()) const override { return false; }
	virtual bool IsModifiedInOtherBranch(const FString& CurrentBranch = FString()) const override { return false; }
	virtual bool IsCheckedOutOrModifiedInOtherBranch(const FString& CurrentBranch = FString()) const override { return false; }
	virtual TArray<FString> GetCheckedOutBranches() const override { return TArray<FString>(); }
	virtual FString GetOtherUserBranchCheckedOuts() const override { return FString(); }
	virtual bool GetOtherBranchHeadModification(FString& HeadBranchOut, FString& ActionOut, int32& HeadChangeListOut) const override { return false; }
	virtual bool IsCurrent() const override;
	virtual bool IsSourceControlled() const override;
	virtual bool IsAdded() const override;
	virtual bool IsDeleted() const override;
	virtual bool IsIgnored() const override;
	virtual bool CanEdit() const override;
	virtual bool CanDelete() const override;
	virtual bool IsUnknown() const override;
	virtual bool IsModified() const override;
	virtual bool CanAdd() const override;
	virtual bool CanRevert() const override;
	virtual bool IsConflicted() const override;

	/** 파일 절대경로 (엔진이 쓰는 형태 그대로 보관). */
	FString LocalFilename;

	/** Content 기준 상대 경로 (예: Foo/Bar.uasset). */
	FString RelativePath;

	EJokateFileState State = EJokateFileState::Unknown;

	/** 마지막으로 상태를 갱신한 시각. */
	FDateTime TimeStamp = FDateTime::Now();

	/** 확정 버전 이력 (최신순). UpdateStatus 의 ShouldUpdateHistory 가 참일 때 채운다. */
	TArray<TSharedRef<FJokateSourceControlRevision, ESPMode::ThreadSafe>> History;
};
