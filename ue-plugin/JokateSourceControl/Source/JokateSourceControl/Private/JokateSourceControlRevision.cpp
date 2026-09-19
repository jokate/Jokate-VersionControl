// Jokate revision control plugin for Unreal Engine.

#include "JokateSourceControlRevision.h"

#include "JokateHttp.h"
#include "JokateSourceControlLog.h"
#include "JokateSourceControlModule.h"

#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"

bool FJokateSourceControlRevision::Get(FString& InOutFilename, EConcurrency::Type InConcurrency) const
{
	if (Rel.IsEmpty() || Sha.IsEmpty())
	{
		// 삭제 리비전은 꺼낼 내용이 없다.
		return false;
	}

	TSharedRef<FJsonObject> Body = MakeShared<FJsonObject>();
	Body->SetStringField(TEXT("rel"), Rel);
	Body->SetStringField(TEXT("sha"), Sha);

	const FString BaseUrl = FJokateSourceControlModule::Get().AccessSettings().GetBaseUrl();
	const FJokateHttpResult Result = FJokateHttp::PostJson(BaseUrl, TEXT("/api/extract"), Body);
	if (!Result.bOk || !Result.Json.IsValid())
	{
		UE_LOG(LogJokateSourceControl, Warning, TEXT("리비전을 꺼내지 못했습니다: %s"), *Rel);
		return false;
	}

	FString ExtractedPath;
	if (!Result.Json->TryGetStringField(TEXT("path"), ExtractedPath) || ExtractedPath.IsEmpty())
	{
		return false;
	}

	if (InOutFilename.IsEmpty())
	{
		InOutFilename = ExtractedPath;
		return true;
	}

	// 엔진이 목적지를 정해 준 경우 그 경로로 복사한다.
	return IFileManager::Get().Copy(*InOutFilename, *ExtractedPath, true, true) == COPY_OK;
}

bool FJokateSourceControlRevision::GetAnnotated(TArray<FAnnotationLine>& OutLines) const
{
	return false;
}

bool FJokateSourceControlRevision::GetAnnotated(FString& InOutFilename) const
{
	return false;
}

const FString& FJokateSourceControlRevision::GetFilename() const
{
	return Filename;
}

int32 FJokateSourceControlRevision::GetRevisionNumber() const
{
	return RevisionNumber;
}

const FString& FJokateSourceControlRevision::GetRevision() const
{
	return RevisionLabel;
}

const FString& FJokateSourceControlRevision::GetDescription() const
{
	return Description;
}

const FString& FJokateSourceControlRevision::GetUserName() const
{
	return UserName;
}

const FString& FJokateSourceControlRevision::GetClientSpec() const
{
	return ClientSpec;
}

const FString& FJokateSourceControlRevision::GetAction() const
{
	return Action;
}

TSharedPtr<ISourceControlRevision, ESPMode::ThreadSafe> FJokateSourceControlRevision::GetBranchSource() const
{
	return nullptr;
}

const FDateTime& FJokateSourceControlRevision::GetDate() const
{
	return Date;
}

int32 FJokateSourceControlRevision::GetCheckInIdentifier() const
{
	return SnapshotId;
}

int32 FJokateSourceControlRevision::GetFileSize() const
{
	return FileSize;
}
