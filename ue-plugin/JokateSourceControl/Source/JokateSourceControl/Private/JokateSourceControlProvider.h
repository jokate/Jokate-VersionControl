// Jokate revision control plugin for Unreal Engine.

#pragma once

#include "CoreMinimal.h"
#include "ISourceControlProvider.h"
#include "ISourceControlState.h"
#include "JokateSourceControlState.h"

class FJokateSourceControlProvider : public ISourceControlProvider
{
public:
	// ISourceControlProvider
	virtual void Init(bool bForceConnection = true) override;
	virtual void Close() override;
	virtual const FName& GetName() const override;
	virtual FText GetStatusText() const override;
	virtual TMap<EStatus, FString> GetStatus() const override;
	virtual bool IsEnabled() const override;
	virtual bool IsAvailable() const override;
	virtual bool QueryStateBranchConfig(const FString& ConfigSrc, const FString& ConfigDest) override { return false; }
	virtual void RegisterStateBranches(const TArray<FString>& BranchNames, const FString& ContentRoot) override {}
	virtual int32 GetStateBranchIndex(const FString& BranchName) const override { return INDEX_NONE; }
	virtual bool GetStateBranchAtIndex(int32 BranchIndex, FString& OutBranchName) const override { return false; }
	virtual ECommandResult::Type GetState(const TArray<FString>& InFiles, TArray<FSourceControlStateRef>& OutState, EStateCacheUsage::Type InStateCacheUsage) override;
	virtual ECommandResult::Type GetState(const TArray<FSourceControlChangelistRef>& InChangelists, TArray<FSourceControlChangelistStateRef>& OutState, EStateCacheUsage::Type InStateCacheUsage) override;
	virtual TArray<FSourceControlStateRef> GetCachedStateByPredicate(TFunctionRef<bool(const FSourceControlStateRef&)> Predicate) const override;
	virtual FDelegateHandle RegisterSourceControlStateChanged_Handle(const FSourceControlStateChanged::FDelegate& SourceControlStateChanged) override;
	virtual void UnregisterSourceControlStateChanged_Handle(FDelegateHandle Handle) override;
	virtual ECommandResult::Type Execute(const FSourceControlOperationRef& InOperation, FSourceControlChangelistPtr InChangelist, const TArray<FString>& InFiles, EConcurrency::Type InConcurrency = EConcurrency::Synchronous, const FSourceControlOperationComplete& InOperationCompleteDelegate = FSourceControlOperationComplete()) override;
	virtual bool CanExecuteOperation(const FSourceControlOperationRef& InOperation) const override;
	virtual bool CanCancelOperation(const FSourceControlOperationRef& InOperation) const override;
	virtual void CancelOperation(const FSourceControlOperationRef& InOperation) override;
	virtual TArray<TSharedRef<class ISourceControlLabel>> GetLabels(const FString& InMatchingSpec) const override;
	virtual TArray<FSourceControlChangelistRef> GetChangelists(EStateCacheUsage::Type InStateCacheUsage) override;
	virtual bool UsesLocalReadOnlyState() const override;
	virtual bool UsesChangelists() const override;
	virtual bool UsesUncontrolledChangelists() const override;
	virtual bool UsesCheckout() const override;
	virtual bool UsesFileRevisions() const override;
	virtual bool UsesSnapshots() const override;
	virtual bool AllowsDiffAgainstDepot() const override;
	virtual TOptional<bool> IsAtLatestRevision() const override;
	virtual TOptional<int> GetNumLocalChanges() const override;
	virtual void Tick() override;
#if SOURCE_CONTROL_WITH_SLATE
	virtual TSharedRef<class SWidget> MakeSettingsWidget() const override;
#endif

private:
	/** 데몬에 /api/ping 을 보내 이 프로젝트에 연결됐는지 확인한다. 게임 스레드에서만 호출. */
	bool CheckConnection(FText& OutError);

	/** 파일 목록의 상태를 데몬에서 받아 캐시에 반영한다(게임 스레드). 파일이 비면 전체. */
	bool RunUpdateStatus(const TArray<FString>& InFiles, FText& OutError, bool bUpdateHistory = false);

	/** 파일 하나의 확정 버전 이력을 GET /api/history 로 채운다. */
	void RunUpdateHistory(const TSharedRef<FJokateSourceControlState, ESPMode::ThreadSafe>& InState);

	/** Submit(확정) — POST /api/confirm */
	bool RunCheckIn(const FSourceControlOperationRef& InOperation, const TArray<FString>& InFiles, FText& OutError);

	/** Revert(변경 버리기) — POST /api/discard */
	bool RunRevert(const FSourceControlOperationRef& InOperation, const TArray<FString>& InFiles, FText& OutError);

	/** Delete — 디스크에서 지우고 상태 갱신 */
	bool RunDelete(const FSourceControlOperationRef& InOperation, const TArray<FString>& InFiles, FText& OutError);

	/** MarkForAdd·Copy — 데몬 호출 없이 상태만 갱신 */
	bool RunTouchStatusOnly(const TArray<FString>& InFiles, FText& OutError);

	TSharedRef<FJokateSourceControlState, ESPMode::ThreadSafe> GetStateInternal(const FString& InFilename);

	/** 상태 캐시 (절대경로 → 상태). */
	TMap<FString, TSharedRef<FJokateSourceControlState, ESPMode::ThreadSafe>> StateCache;

	FSourceControlStateChanged OnSourceControlStateChanged;

	bool bAvailable = false;
	bool bStateChangedPending = false;

	/** 데몬 재시작 등으로 끊겼을 때 Tick 에서 주기적으로 다시 붙는다. */
	void TryReconnect();
	bool bWasConnected = false;
	bool bReconnectInFlight = false;
	double LastReconnectAttempt = 0.0;
	TSharedRef<bool, ESPMode::ThreadSafe> AliveFlag = MakeShared<bool, ESPMode::ThreadSafe>(true);

	FString HeadFix;
	FText LastError;
};
