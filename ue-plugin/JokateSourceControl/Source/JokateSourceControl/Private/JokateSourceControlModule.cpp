// Jokate revision control plugin for Unreal Engine.

#include "JokateSourceControlModule.h"

#include "JokateSourceControlLog.h"
#include "Features/IModularFeatures.h"
#include "Modules/ModuleManager.h"

DEFINE_LOG_CATEGORY(LogJokateSourceControl);

#define LOCTEXT_NAMESPACE "JokateSourceControl"

void FJokateSourceControlModule::StartupModule()
{
	Settings.LoadSettings();

	IModularFeatures::Get().RegisterModularFeature("SourceControl", &Provider);

	UE_LOG(LogJokateSourceControl, Log, TEXT("Jokate 리비전 컨트롤 프로바이더 등록 (포트 %d)"), Settings.GetPort());
}

void FJokateSourceControlModule::ShutdownModule()
{
	Provider.Close();

	IModularFeatures::Get().UnregisterModularFeature("SourceControl", &Provider);
}

void FJokateSourceControlModule::SaveSettings()
{
	Settings.SaveSettings();
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FJokateSourceControlModule, JokateSourceControl);
