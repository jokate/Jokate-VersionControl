// Jokate revision control plugin for Unreal Engine.

#include "JokateSourceControlModule.h"

#include "JokateSourceControlLog.h"
#include "Features/IModularFeatures.h"
#include "Framework/Commands/UIAction.h"
#include "HAL/PlatformProcess.h"
#include "ISourceControlModule.h"
#include "ISourceControlProvider.h"
#include "Modules/ModuleManager.h"
#include "SourceControlOperations.h"
#include "Textures/SlateIcon.h"
#include "ToolMenus.h"

DEFINE_LOG_CATEGORY(LogJokateSourceControl);

#define LOCTEXT_NAMESPACE "JokateSourceControl"

void FJokateSourceControlModule::StartupModule()
{
	Settings.LoadSettings();

	IModularFeatures::Get().RegisterModularFeature("SourceControl", &Provider);

	UToolMenus::RegisterStartupCallback(
		FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FJokateSourceControlModule::RegisterMenus));

	UE_LOG(LogJokateSourceControl, Log, TEXT("Jokate 리비전 컨트롤 프로바이더 등록 (포트 %d)"), Settings.GetPort());
}

void FJokateSourceControlModule::ShutdownModule()
{
	UToolMenus::UnRegisterStartupCallback(this);
	UToolMenus::UnregisterOwner(this);

	Provider.Close();

	IModularFeatures::Get().UnregisterModularFeature("SourceControl", &Provider);
}

void FJokateSourceControlModule::RegisterMenus()
{
	// 파이썬 브릿지가 없어도 쓸 수 있는 메뉴 — 브릿지의 Tools > Jokate 와 섹션을 나눈다.
	FToolMenuOwnerScoped OwnerScoped(this);

	UToolMenu* Menu = UToolMenus::Get()->ExtendMenu("LevelEditor.MainMenu.Tools");
	if (Menu == nullptr)
	{
		return;
	}

	FToolMenuSection& Section = Menu->FindOrAddSection(
		"JokateSourceControl", LOCTEXT("JokateMenuSection", "Jokate 리비전 컨트롤"));

	Section.AddMenuEntry(
		"JokateOpenTimeline",
		LOCTEXT("JokateOpenTimeline", "타임라인 열기"),
		LOCTEXT("JokateOpenTimelineTip", "Jokate 타임라인 웹 UI 를 브라우저에서 연다 (데몬이 켜져 있어야 함)"),
		FSlateIcon(),
		FUIAction(FExecuteAction::CreateLambda([]()
		{
			FJokateSourceControlModule& Module = FJokateSourceControlModule::Get();
			Module.AccessSettings().LoadSettings();
			const FString Url = FString::Printf(TEXT("http://127.0.0.1:%d/"), Module.AccessSettings().GetPort());
			FPlatformProcess::LaunchURL(*Url, nullptr, nullptr);
		})));

	Section.AddMenuEntry(
		"JokateRefreshStatus",
		LOCTEXT("JokateRefreshStatus", "상태 새로 고침"),
		LOCTEXT("JokateRefreshStatusTip", "모든 애셋의 Jokate 상태를 데몬에서 다시 받아온다"),
		FSlateIcon(),
		FUIAction(FExecuteAction::CreateLambda([]()
		{
			ISourceControlProvider& CurrentProvider = ISourceControlModule::Get().GetProvider();
			if (CurrentProvider.GetName() != FName("Jokate"))
			{
				UE_LOG(LogJokateSourceControl, Warning,
					TEXT("리비전 컨트롤 프로바이더가 Jokate 가 아닙니다. 상태를 새로 고치지 않습니다."));
				return;
			}
			CurrentProvider.Execute(
				ISourceControlOperation::Create<FUpdateStatus>(),
				FSourceControlChangelistPtr(),
				TArray<FString>(),
				EConcurrency::Asynchronous);
		})));
}

void FJokateSourceControlModule::SaveSettings()
{
	Settings.SaveSettings();
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FJokateSourceControlModule, JokateSourceControl);
