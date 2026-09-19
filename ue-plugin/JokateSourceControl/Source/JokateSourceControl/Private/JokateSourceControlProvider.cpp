// Jokate revision control plugin for Unreal Engine.

#include "JokateSourceControlProvider.h"

#include "JokateHttp.h"
#include "JokateSourceControlLog.h"
#include "JokateSourceControlModule.h"
#include "JokateSourceControlUtils.h"

#include "Async/Async.h"
#include "Dom/JsonObject.h"
#include "HAL/PlatformProcess.h"
#include "ISourceControlModule.h"
#include "Misc/Paths.h"
#include "SourceControlHelpers.h"
#include "SourceControlOperations.h"

#if SOURCE_CONTROL_WITH_SLATE
#include "Widgets/DeclarativeSyntaxSupport.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Text/STextBlock.h"
#endif

#define LOCTEXT_NAMESPACE "JokateSourceControl"

namespace
{
	const FName JokateProviderName("Jokate");
}

void FJokateSourceControlProvider::Init(bool bForceConnection)
{
	FJokateSourceControlModule::Get().AccessSettings().LoadSettings();

	if (bForceConnection)
	{
		FText Error;
		bAvailable = CheckConnection(Error);
		LastError = Error;
		if (!bAvailable)
		{
			UE_LOG(LogJokateSourceControl, Warning, TEXT("Jokate 데몬에 연결하지 못했습니다: %s"), *Error.ToString());
		}
	}
}

void FJokateSourceControlProvider::Close()
{
	StateCache.Empty();
	bAvailable = false;
	bStateChangedPending = false;
	HeadFix.Reset();
}

const FName& FJokateSourceControlProvider::GetName() const
{
	return JokateProviderName;
}

FText FJokateSourceControlProvider::GetStatusText() const
{
	const int32 Port = FJokateSourceControlModule::Get().AccessSettings().GetPort();

	FFormatNamedArguments Args;
	Args.Add(TEXT("IsAvailable"), bAvailable ? LOCTEXT("Connected", "연결됨") : LOCTEXT("NotConnected", "연결 안 됨"));
	Args.Add(TEXT("Port"), FText::AsNumber(Port, &FNumberFormattingOptions::DefaultNoGrouping()));
	Args.Add(TEXT("HeadFix"), HeadFix.IsEmpty() ? LOCTEXT("NoFix", "(확정 없음)") : FText::FromString(HeadFix));

	return FText::Format(
		LOCTEXT("JokateStatusText", "프로바이더: Jokate\n상태: {IsAvailable}\n포트: {Port}\n마지막 확정: {HeadFix}"), Args);
}

TMap<ISourceControlProvider::EStatus, FString> FJokateSourceControlProvider::GetStatus() const
{
	TMap<EStatus, FString> Result;
	Result.Add(EStatus::Enabled, IsEnabled() ? TEXT("Yes") : TEXT("No"));
	Result.Add(EStatus::Connected, IsAvailable() ? TEXT("Yes") : TEXT("No"));
	Result.Add(EStatus::Port, FString::FromInt(FJokateSourceControlModule::Get().AccessSettings().GetPort()));
	Result.Add(EStatus::Repository, FPaths::ConvertRelativePathToFull(FPaths::ProjectDir()));
	return Result;
}

bool FJokateSourceControlProvider::IsEnabled() const
{
	return true;
}

bool FJokateSourceControlProvider::IsAvailable() const
{
	return bAvailable;
}

bool FJokateSourceControlProvider::CheckConnection(FText& OutError)
{
	const FString BaseUrl = FJokateSourceControlModule::Get().AccessSettings().GetBaseUrl();
	const FJokateHttpResult Result = FJokateHttp::GetJson(BaseUrl, TEXT("/api/ping"));
	if (!Result.bOk || !Result.Json.IsValid())
	{
		OutError = Result.ErrorText.IsEmpty()
			? LOCTEXT("PingFailed", "Jokate 데몬이 응답하지 않습니다. start.bat 을 실행하세요.")
			: Result.ErrorText;
		return false;
	}

	FString Root;
	Result.Json->TryGetStringField(TEXT("root"), Root);

	const FString ProjectDir = JokateSourceControlUtils::NormalizeFilename(FPaths::ProjectDir());
	FString RootNormalized = JokateSourceControlUtils::NormalizeFilename(Root);
	RootNormalized.RemoveFromEnd(TEXT("/"));
	FString ProjectNormalized = ProjectDir;
	ProjectNormalized.RemoveFromEnd(TEXT("/"));

	if (!Root.IsEmpty() && !RootNormalized.Equals(ProjectNormalized, ESearchCase::IgnoreCase))
	{
		OutError = FText::Format(
			LOCTEXT("WrongProject", "데몬이 다른 프로젝트({0})를 보고 있습니다."), FText::FromString(RootNormalized));
		return false;
	}

	OutError = FText::GetEmpty();
	return true;
}

TSharedRef<FJokateSourceControlState, ESPMode::ThreadSafe> FJokateSourceControlProvider::GetStateInternal(const FString& InFilename)
{
	const FString Key = JokateSourceControlUtils::NormalizeFilename(InFilename);
	if (TSharedRef<FJokateSourceControlState, ESPMode::ThreadSafe>* Found = StateCache.Find(Key))
	{
		return *Found;
	}

	TSharedRef<FJokateSourceControlState, ESPMode::ThreadSafe> NewState = MakeShared<FJokateSourceControlState, ESPMode::ThreadSafe>(Key);
	NewState->RelativePath = JokateSourceControlUtils::ToContentRelative(Key);
	if (NewState->RelativePath.IsEmpty())
	{
		NewState->State = EJokateFileState::Untracked;
	}
	StateCache.Add(Key, NewState);
	return NewState;
}

bool FJokateSourceControlProvider::RunUpdateStatus(const TArray<FString>& InFiles, FText& OutError)
{
	check(IsInGameThread());

	TArray<TSharedPtr<FJsonValue>> RelValues;
	TArray<FString> RequestedRels;
	for (const FString& File : InFiles)
	{
		TSharedRef<FJokateSourceControlState, ESPMode::ThreadSafe> State = GetStateInternal(File);
		if (State->RelativePath.IsEmpty())
		{
			State->State = EJokateFileState::Untracked;
			State->TimeStamp = FDateTime::Now();
			continue;
		}
		RequestedRels.Add(State->RelativePath);
		RelValues.Add(MakeShared<FJsonValueString>(State->RelativePath));
	}

	if (InFiles.Num() > 0 && RequestedRels.Num() == 0)
	{
		// Content 밖의 파일만 요청된 경우 — 데몬을 부를 필요가 없다.
		bStateChangedPending = true;
		return true;
	}

	TSharedRef<FJsonObject> Body = MakeShared<FJsonObject>();
	Body->SetArrayField(TEXT("rels"), RelValues);

	const FString BaseUrl = FJokateSourceControlModule::Get().AccessSettings().GetBaseUrl();
	const FJokateHttpResult Result = FJokateHttp::PostJson(BaseUrl, TEXT("/api/states"), Body);
	if (!Result.bOk)
	{
		OutError = Result.ErrorText.IsEmpty() ? LOCTEXT("StatesFailed", "상태를 받아오지 못했습니다.") : Result.ErrorText;
		bAvailable = false;
		return false;
	}

	TMap<FString, EJokateFileState> States;
	FString NewHeadFix;
	if (!JokateSourceControlUtils::ParseStatesResponse(Result.Json, States, NewHeadFix))
	{
		OutError = LOCTEXT("StatesParseFailed", "데몬 응답을 해석하지 못했습니다.");
		return false;
	}

	HeadFix = NewHeadFix;
	bAvailable = true;

	const FDateTime Now = FDateTime::Now();
	for (const TPair<FString, EJokateFileState>& Pair : States)
	{
		const FString Absolute = JokateSourceControlUtils::FromContentRelative(Pair.Key);
		TSharedRef<FJokateSourceControlState, ESPMode::ThreadSafe> State = GetStateInternal(Absolute);
		State->RelativePath = Pair.Key;
		State->State = Pair.Value;
		State->TimeStamp = Now;
	}

	// 응답에 없는 요청 파일은 추적 안 함으로 본다.
	for (const FString& Rel : RequestedRels)
	{
		if (!States.Contains(Rel))
		{
			TSharedRef<FJokateSourceControlState, ESPMode::ThreadSafe> State = GetStateInternal(JokateSourceControlUtils::FromContentRelative(Rel));
			State->State = EJokateFileState::Untracked;
			State->TimeStamp = Now;
		}
	}

	bStateChangedPending = true;
	OnSourceControlStateChanged.Broadcast();
	return true;
}

ECommandResult::Type FJokateSourceControlProvider::GetState(const TArray<FString>& InFiles, TArray<FSourceControlStateRef>& OutState, EStateCacheUsage::Type InStateCacheUsage)
{
	if (!IsEnabled())
	{
		return ECommandResult::Failed;
	}

	const TArray<FString> AbsoluteFiles = SourceControlHelpers::AbsoluteFilenames(InFiles);

	if (InStateCacheUsage == EStateCacheUsage::ForceUpdate)
	{
		FText Error;
		RunUpdateStatus(AbsoluteFiles, Error);
	}

	for (const FString& File : AbsoluteFiles)
	{
		OutState.Add(GetStateInternal(File));
	}

	return ECommandResult::Succeeded;
}

ECommandResult::Type FJokateSourceControlProvider::GetState(const TArray<FSourceControlChangelistRef>& InChangelists, TArray<FSourceControlChangelistStateRef>& OutState, EStateCacheUsage::Type InStateCacheUsage)
{
	// Jokate 는 체인지리스트를 쓰지 않는다.
	return ECommandResult::Failed;
}

TArray<FSourceControlStateRef> FJokateSourceControlProvider::GetCachedStateByPredicate(TFunctionRef<bool(const FSourceControlStateRef&)> Predicate) const
{
	TArray<FSourceControlStateRef> Result;
	for (const auto& Pair : StateCache)
	{
		FSourceControlStateRef State = Pair.Value;
		if (Predicate(State))
		{
			Result.Add(State);
		}
	}
	return Result;
}

FDelegateHandle FJokateSourceControlProvider::RegisterSourceControlStateChanged_Handle(const FSourceControlStateChanged::FDelegate& SourceControlStateChanged)
{
	return OnSourceControlStateChanged.Add(SourceControlStateChanged);
}

void FJokateSourceControlProvider::UnregisterSourceControlStateChanged_Handle(FDelegateHandle Handle)
{
	OnSourceControlStateChanged.Remove(Handle);
}

ECommandResult::Type FJokateSourceControlProvider::Execute(const FSourceControlOperationRef& InOperation, FSourceControlChangelistPtr InChangelist, const TArray<FString>& InFiles, EConcurrency::Type InConcurrency, const FSourceControlOperationComplete& InOperationCompleteDelegate)
{
	const FName OperationName = InOperation->GetName();
	const TArray<FString> AbsoluteFiles = SourceControlHelpers::AbsoluteFilenames(InFiles);

	if (OperationName != "Connect" && OperationName != "UpdateStatus")
	{
		InOperation->AddErrorMessge(FText::Format(
			LOCTEXT("UnsupportedOperation", "Jokate 는 아직 '{0}' 작업을 지원하지 않습니다. 타임라인 웹에서 확정·되돌리기를 해 주세요."),
			FText::FromName(OperationName)));
		InOperationCompleteDelegate.ExecuteIfBound(InOperation, ECommandResult::Failed);
		return ECommandResult::Failed;
	}

	// 실제 작업(HTTP + 캐시 갱신)은 언제나 게임 스레드에서 끝낸다.
	auto RunWork = [this, InOperation, AbsoluteFiles, OperationName, InOperationCompleteDelegate]()
	{
		FText Error;
		bool bSucceeded = false;

		if (OperationName == "Connect")
		{
			bAvailable = CheckConnection(Error);
			bSucceeded = bAvailable;
			if (bSucceeded)
			{
				FText StatusError;
				RunUpdateStatus(TArray<FString>(), StatusError);
			}
		}
		else
		{
			bSucceeded = RunUpdateStatus(AbsoluteFiles, Error);
		}

		if (!bSucceeded && !Error.IsEmpty())
		{
			InOperation->AddErrorMessge(Error);
			LastError = Error;
		}

		const ECommandResult::Type CommandResult = bSucceeded ? ECommandResult::Succeeded : ECommandResult::Failed;
		InOperationCompleteDelegate.ExecuteIfBound(InOperation, CommandResult);
		return CommandResult;
	};

	if (InConcurrency == EConcurrency::Asynchronous)
	{
		// 호출자를 막지 않도록 스레드풀로 넘긴 뒤, 캐시 갱신·델리게이트는 게임 스레드로 되돌린다.
		Async(EAsyncExecution::ThreadPool, [RunWork]()
		{
			AsyncTask(ENamedThreads::GameThread, [RunWork]() { RunWork(); });
		});
		return ECommandResult::Succeeded;
	}

	if (!IsInGameThread())
	{
		// 동기 호출인데 게임 스레드가 아니면 그대로 여기서 처리한다.
		return RunWork();
	}

	return RunWork();
}

bool FJokateSourceControlProvider::CanExecuteOperation(const FSourceControlOperationRef& InOperation) const
{
	const FName OperationName = InOperation->GetName();
	return OperationName == "Connect" || OperationName == "UpdateStatus";
}

bool FJokateSourceControlProvider::CanCancelOperation(const FSourceControlOperationRef& InOperation) const
{
	return false;
}

void FJokateSourceControlProvider::CancelOperation(const FSourceControlOperationRef& InOperation)
{
	// 취소를 지원하지 않는다.
}

TArray<TSharedRef<class ISourceControlLabel>> FJokateSourceControlProvider::GetLabels(const FString& InMatchingSpec) const
{
	return TArray<TSharedRef<class ISourceControlLabel>>();
}

TArray<FSourceControlChangelistRef> FJokateSourceControlProvider::GetChangelists(EStateCacheUsage::Type InStateCacheUsage)
{
	return TArray<FSourceControlChangelistRef>();
}

bool FJokateSourceControlProvider::UsesLocalReadOnlyState() const { return false; }
bool FJokateSourceControlProvider::UsesChangelists() const { return false; }
bool FJokateSourceControlProvider::UsesUncontrolledChangelists() const { return false; }
bool FJokateSourceControlProvider::UsesCheckout() const { return false; }
bool FJokateSourceControlProvider::UsesFileRevisions() const { return true; }
bool FJokateSourceControlProvider::UsesSnapshots() const { return false; }
bool FJokateSourceControlProvider::AllowsDiffAgainstDepot() const { return true; }

TOptional<bool> FJokateSourceControlProvider::IsAtLatestRevision() const
{
	return true;
}

TOptional<int> FJokateSourceControlProvider::GetNumLocalChanges() const
{
	int32 Count = 0;
	for (const auto& Pair : StateCache)
	{
		if (Pair.Value->CanCheckIn())
		{
			++Count;
		}
	}
	return Count;
}

void FJokateSourceControlProvider::Tick()
{
	if (bStateChangedPending)
	{
		bStateChangedPending = false;
		OnSourceControlStateChanged.Broadcast();
	}
}

#if SOURCE_CONTROL_WITH_SLATE
TSharedRef<class SWidget> FJokateSourceControlProvider::MakeSettingsWidget() const
{
	const int32 Port = FJokateSourceControlModule::Get().AccessSettings().GetPort();
	const bool bConnected = bAvailable;

	return SNew(SBox)
		.Padding(4.0f)
		[
			SNew(SVerticalBox)
			+ SVerticalBox::Slot()
			.AutoHeight()
			.Padding(0.0f, 2.0f)
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot()
				.AutoWidth()
				.VAlign(VAlign_Center)
				.Padding(0.0f, 0.0f, 8.0f, 0.0f)
				[
					SNew(STextBlock).Text(LOCTEXT("PortLabel", "데몬 포트"))
				]
				+ SHorizontalBox::Slot()
				.FillWidth(1.0f)
				[
					SNew(SEditableTextBox)
					.Text(FText::AsNumber(Port, &FNumberFormattingOptions::DefaultNoGrouping()))
					.ToolTipText(LOCTEXT("PortTooltip", "Jokate 데몬이 듣는 포트 (기본 8765)"))
					.OnTextCommitted_Lambda([](const FText& InText, ETextCommit::Type)
					{
						const int32 NewPort = FCString::Atoi(*InText.ToString());
						if (NewPort > 0)
						{
							FJokateSourceControlModule::Get().AccessSettings().SetPort(NewPort);
							FJokateSourceControlModule::Get().AccessSettings().SaveSettings();
						}
					})
				]
			]
			+ SVerticalBox::Slot()
			.AutoHeight()
			.Padding(0.0f, 2.0f)
			[
				SNew(SButton)
				.Text(LOCTEXT("OpenTimeline", "타임라인 열기"))
				.OnClicked_Lambda([]()
				{
					const FString Url = FJokateSourceControlModule::Get().AccessSettings().GetBaseUrl();
					FPlatformProcess::LaunchURL(*Url, nullptr, nullptr);
					return FReply::Handled();
				})
			]
			+ SVerticalBox::Slot()
			.AutoHeight()
			.Padding(0.0f, 4.0f)
			[
				SNew(STextBlock)
				.AutoWrapText(true)
				.Visibility(bConnected ? EVisibility::Collapsed : EVisibility::Visible)
				.Text(LOCTEXT("DaemonHint", "데몬이 실행 중이 아닙니다. start.bat 을 실행하거나 에디터 툴 메뉴 Jokate > 데몬 시작"))
			]
		];
}
#endif

#undef LOCTEXT_NAMESPACE
